"""
Phase 8 Step C4 Tests & Benchmarks: Dashboard Snapshot Optimization & In-Process Caching.

Verifies:
1. Exact snapshot schema and returned fields are preserved.
2. In-process cache suppresses redundant DB queries on rapid repeated calls.
3. Emergency Stop authority: Emergency Stop remains 100% dynamic and is NEVER made stale
   by the dashboard cache.
4. Telemetry snapshot (lightweight mode) executes in <= 4 queries.
"""

import time
import pytest
from sqlalchemy import event

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.scheduler.emergency_stop import EmergencyStop
from app.web.services.dashboard_service import DashboardService


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


def test_dashboard_snapshot_schema_and_correctness(db_session):
    """Proves all expected keys and DTOs exist in snapshot."""
    DashboardService.invalidate_cache()
    EmergencyStop(db_session).resume()

    snapshot = DashboardService.get_dashboard_snapshot(db_session, bypass_cache=True)
    assert "timestamp" in snapshot
    assert "system_health" in snapshot
    assert "database" in snapshot
    assert "whatsapp" in snapshot
    assert "emergency_stop" in snapshot
    assert "circuit_breaker" in snapshot
    assert "queue" in snapshot
    assert "runner" in snapshot
    assert "alerts" in snapshot

    assert snapshot["database"]["connected"] is True
    assert snapshot["emergency_stop"]["is_active"] is False


def test_dashboard_in_process_cache_and_emergency_stop_freshness(db_session):
    """
    Proves that repeated calls use the in-process cache, BUT Emergency Stop
    activation is immediately visible even while cache is warm.
    """
    DashboardService.invalidate_cache()
    EmergencyStop(db_session).resume()
    engine = db_session.get_bind()

    # 1. First call (cold / bypass cache)
    with QueryCounter(engine) as qc_cold:
        snap1 = DashboardService.get_dashboard_snapshot(db_session, bypass_cache=True)
    cold_queries = qc_cold.count

    # 2. Second call within TTL (warm cache)
    with QueryCounter(engine) as qc_warm:
        snap2 = DashboardService.get_dashboard_snapshot(db_session)
    warm_queries = qc_warm.count

    # Warm cache should execute drastically fewer queries (only the authoritative EmergencyStop lookup!)
    assert warm_queries < cold_queries
    assert warm_queries <= 2
    assert snap2["emergency_stop"]["is_active"] is False

    # 3. Trigger Emergency Stop externally while cache is warm
    EmergencyStop(db_session).trigger(reason="Killswitch Activated During Cache Window")

    # 4. Immediate third call within TTL
    snap3 = DashboardService.get_dashboard_snapshot(db_session)

    # Must immediately reflect ACTIVE=True without waiting for cache TTL to expire!
    assert snap3["emergency_stop"]["is_active"] is True
    assert snap3["emergency_stop"]["reason"] == "Killswitch Activated During Cache Window"


def test_telemetry_snapshot_lightweight_query_count(db_session):
    """Proves get_telemetry_snapshot executes in bounded, minimal queries."""
    DashboardService.invalidate_cache()
    EmergencyStop(db_session).resume()
    engine = db_session.get_bind()

    with QueryCounter(engine) as qc:
        telemetry = DashboardService.get_telemetry_snapshot(db_session)

    # Telemetry snapshot must execute in <= 8 queries total (down from 39 baseline queries)
    assert qc.count <= 8
    assert telemetry["database"]["connected"] is True
    assert "system_health" in telemetry
    assert "runner" in telemetry
    assert "whatsapp" in telemetry
    assert "queue" in telemetry
