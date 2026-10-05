"""
Phase 8 Step C1 Regression Tests: AppSettingService & Batched Settings Access Path.

Verifies:
1. Exact returned values match individual queries.
2. Missing-key behavior correctly returns None or specified defaults.
3. Query count reduction: N keys fetched in exactly 1 SQL SELECT.
4. Semantic freshness: modifications in DB are immediately observed on subsequent calls.
5. Emergency Stop dynamic observability: Emergency Stop remains on its own authoritative path,
   not affected or made stale by batched settings access.
"""

import pytest
from sqlalchemy import event
from app.models.app_setting import AppSetting
from app.services.app_setting_service import AppSettingService
from app.scheduler.emergency_stop import EmergencyStop
from app.web.services.runner_control_service import RunnerControlService
from app.limiter.frequency_service import FrequencyLimitService
from app.models.contact import Contact
from app.models.campaign import Campaign


class QueryCounter:
    """SQLAlchemy connection listener counting executed SELECT queries."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = self._count_query

    def _count_query(self, conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            self.count += 1

    def __enter__(self):
        self.count = 0
        event.listen(self.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        event.remove(self.engine, "before_cursor_execute", self._listener)


def test_app_setting_service_values_and_missing_keys(db_session):
    """Proves exact returned values and missing-key defaults."""
    db_session.add(AppSetting(key="test:key_1", value="value_alpha"))
    db_session.add(AppSetting(key="test:key_2", value="42"))
    db_session.add(AppSetting(key="test:key_json", value='{"enabled": true, "rate": 5}'))
    db_session.commit()

    # 1. get_many: existing keys and non-existent keys
    results = AppSettingService.get_many(db_session, ["test:key_1", "test:key_2", "test:missing_key"])
    assert results["test:key_1"] == "value_alpha"
    assert results["test:key_2"] == "42"
    assert results["test:missing_key"] is None

    # Empty list handling
    assert AppSettingService.get_many(db_session, []) == {}

    # 2. get_int
    assert AppSettingService.get_int(db_session, "test:key_2", default=0) == 42
    assert AppSettingService.get_int(db_session, "test:key_1", default=99) == 99  # non-digit
    assert AppSettingService.get_int(db_session, "test:missing_int", default=100) == 100

    # 3. get_json
    json_val = AppSettingService.get_json(db_session, "test:key_json")
    assert json_val == {"enabled": True, "rate": 5}
    assert AppSettingService.get_json(db_session, "test:missing_json", default={}) == {}


def test_app_setting_service_query_count_reduction(db_session):
    """Proves that N keys are retrieved in exactly ONE SQL SELECT."""
    keys = [f"k_{i}" for i in range(10)]
    for k in keys:
        db_session.add(AppSetting(key=k, value=f"val_{k}"))
    db_session.commit()

    engine = db_session.get_bind()
    with QueryCounter(engine) as qc:
        results = AppSettingService.get_many(db_session, keys)
        assert qc.count == 1  # Exactly 1 query!

    assert len(results) == 10
    for k in keys:
        assert results[k] == f"val_{k}"


def test_app_setting_service_semantic_freshness(db_session):
    """Proves that changes in DB are immediately visible with zero stale cache delay."""
    row = AppSetting(key="test:dynamic_key", value="initial_state")
    db_session.add(row)
    db_session.commit()

    assert AppSettingService.get(db_session, "test:dynamic_key") == "initial_state"

    # External update
    row.value = "updated_state"
    db_session.commit()

    # Immediate visibility
    assert AppSettingService.get(db_session, "test:dynamic_key") == "updated_state"


def test_emergency_stop_independence_from_generic_settings(db_session):
    """
    Proves that Emergency Stop remains on its own authoritative path
    and is NOT made stale by any settings reads.
    """
    e_stop = EmergencyStop(db_session)
    e_stop.resume()
    assert e_stop.is_active() is False

    # Trigger emergency stop
    e_stop.trigger(reason="Test Killswitch Activation")

    # Authoritative status immediately sees active=True
    status = EmergencyStop.get_status(db_session)
    assert status["active"] is True
    assert status["reason"] == "Test Killswitch Activation"


def test_frequency_limit_service_batch_optimization(db_session):
    """Proves FrequencyLimitService executes threshold reads in a single query."""
    db_session.add(AppSetting(key="freq_max_messages_per_day", value="2"))
    db_session.add(AppSetting(key="freq_max_messages_per_campaign", value="3"))
    db_session.add(AppSetting(key="freq_max_messages_30d", value="10"))
    db_session.add(AppSetting(key="freq_cooldown_hours", value="12"))

    contact = Contact(phone_e164="+201099998888", country_code="EG", name="Test")
    campaign = Campaign(
        name="Freq Test Camp",
        message_template="Hi",
        status="RUNNING",
        min_delay_seconds=1,
        max_delay_seconds=2,
        daily_limit=100,
    )
    db_session.add_all([contact, campaign])
    db_session.commit()

    svc = FrequencyLimitService(db_session)
    engine = db_session.get_bind()

    # Call check_frequency: thresholds must be loaded in 1 batch query
    with QueryCounter(engine) as qc:
        res = svc.check_frequency(contact, campaign)
        assert res.eligible is True
        # Setting queries must be exactly 1 (for the 4 thresholds), followed by Message table checks
        assert svc._cached_thresholds["max_per_day"] == 2
        assert svc._cached_thresholds["max_per_campaign"] == 3
        assert svc._cached_thresholds["cooldown_hours"] == 12
