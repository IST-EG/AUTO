import time
import pytest
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.scheduler.emergency_stop import EmergencyStop, EmergencyStopTriggered


def test_emergency_stop_lifecycle(db_session):
    es = EmergencyStop(db_session)
    assert not es.is_active()

    # Trigger
    es.trigger(reason="Test emergency stop")
    assert es.is_active()

    # Check persistence in AppSetting
    setting = db_session.query(AppSetting).filter(AppSetting.key == EmergencyStop.SETTING_KEY).first()
    assert setting is not None
    assert setting.value == "true"

    # Check AuditLog
    log = db_session.query(AuditLog).filter(AuditLog.event_type == "EMERGENCY_STOP_ACTIVATED").first()
    assert log is not None
    assert log.status == "STOPPED"

    # Resume
    es.resume(reason="Test resumption")
    assert not es.is_active()

    setting = db_session.query(AppSetting).filter(AppSetting.key == EmergencyStop.SETTING_KEY).first()
    assert setting.value == "false"


def test_emergency_stop_safe_cancellation_point(db_session):
    es = EmergencyStop(db_session)
    # Should not raise when inactive
    es.check_safe_cancellation_point()

    es.trigger()
    with pytest.raises(EmergencyStopTriggered):
        es.check_safe_cancellation_point()


def test_emergency_stop_latency_under_500ms(db_session):
    """
    Verifies that target emergency-stop detection latency is < 500ms under normal worker conditions.
    """
    es = EmergencyStop(db_session)

    # Measure time to trigger and detect cancellation
    start_time = time.perf_counter()

    es.trigger(reason="Latency benchmark test")
    is_stopped = es.is_active()

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert is_stopped is True
    # Target emergency-stop latency is <500ms
    assert elapsed_ms < 500.0, f"Emergency stop latency was {elapsed_ms:.2f}ms, exceeding 500ms target"
