"""
Phase 8 Step B: Emergency Stop Regression Test Suite.

Proves:
1. Emergency Stop OFF -> runner can proceed with message processing.
2. Emergency Stop ON -> runner refuses dispatch and pauses.
3. Emergency Stop activated AFTER runner startup -> running runner detects it at a safe
   cancellation/dispatch boundary without requiring a process restart.
4. Health, preflight, analytics, Control Plane, and WorkerDaemon all report the exact same Emergency Stop state.
5. No stale cached Emergency Stop state can survive activation beyond TTL / force_refresh.
6. All tests use mocks/fakes; zero real WhatsApp messages sent.
"""

import time
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.cli.exit_codes import ExitCode
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.queue.state_machine import QueueState
from app.readiness.health import evaluate_system_health, HealthState
from app.readiness.preflight import check_emergency_stop_status
from app.runner.production_runner import ProductionRunner
from app.runner.worker_daemon import WorkerDaemon
from app.scheduler.emergency_stop import EmergencyStop, EmergencyStopTriggered
from app.services.analytics_service import AnalyticsService
from app.web.services.dashboard_service import DashboardService
from app.web.services.runner_control_service import RunnerControlService


from app.providers.mock_provider import MockMessageProvider
from app.runner.lifecycle import RunnerLifecycleState
from app.runner.process_lock import ProcessLock
from app.readiness.preflight import PreflightResult


@pytest.fixture
def test_setup(db_session, tmp_path):
    """Sets up a clean campaign, contact, and queued message."""
    # Ensure emergency stop is clear
    db_session.query(AppSetting).filter(AppSetting.key == EmergencyStop.SETTING_KEY).delete()
    db_session.commit()

    camp = Campaign(
        name="Phase8_EStop_Test_Campaign",
        message_template="Hello {{name}}",
        status="RUNNING",
        min_delay_seconds=1,
        max_delay_seconds=1,
        error_threshold=5,
    )
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Alice", phone_e164="+10000000001", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Alice",
        status=QueueState.QUEUED,
        idempotency_key=f"phase8_estop_msg_{time.time()}"
    )
    db_session.add(msg)
    db_session.commit()

    lock_file = str(tmp_path / "test_phase8_estop.lock")
    return camp, contact, msg, lock_file


def test_emergency_stop_off_allows_runner_dispatch(db_session, test_setup):
    """1. Emergency Stop OFF -> runner proceeds with dispatch."""
    camp, contact, msg, lock_file = test_setup
    provider = MockMessageProvider(default_success=True)

    runner = ProductionRunner(
        db=db_session,
        campaign_id=camp.id,
        provider=provider,
        poll_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = runner.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    # Message must be sent
    db_session.refresh(msg)
    assert msg.status == QueueState.SENT
    assert len(provider.sent_messages) == 1


def test_emergency_stop_on_blocks_runner_dispatch(db_session, test_setup):
    """2. Emergency Stop ON -> runner refuses dispatch and fails preflight with EMERGENCY_STOP_ACTIVE."""
    camp, contact, msg, lock_file = test_setup

    # Engage emergency stop before runner
    e_stop = EmergencyStop(db_session)
    e_stop.trigger(reason="Test Killswitch Engaged")

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(
        db=db_session,
        campaign_id=camp.id,
        provider=provider,
        poll_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = runner.start(max_iterations=1)
    # Runner halts immediately at preflight without dispatching
    assert exit_code == ExitCode.EMERGENCY_STOP_ACTIVE

    # Message must remain QUEUED; no provider calls
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert len(provider.sent_messages) == 0


def test_emergency_stop_activated_after_runner_startup_detected(db_session, test_setup):
    """
    3. Emergency Stop activated AFTER runner startup -> running runner detects it at a safe
       cancellation/dispatch boundary without requiring a process restart.
    """
    camp, contact, msg, lock_file = test_setup

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(
        db=db_session,
        campaign_id=camp.id,
        provider=provider,
        poll_interval=0.01,
        lock_file=lock_file,
    )

    # Runner starts with emergency stop INACTIVE; its local instance has cached False
    assert runner.emergency_stop.is_active() is False

    # Simulate an external operator activating emergency stop in the DB via another session/controller
    external_e_stop = EmergencyStop(db_session)
    external_e_stop.trigger(reason="Operator killed system while runner was looping")

    # Force expiration of runner's short TTL cache by simulating time elapsed > 1.0s
    runner.emergency_stop._last_checked_mono = time.monotonic() - 2.0

    # The running runner's instance MUST now see active=True without process restart!
    assert runner.emergency_stop.is_active() is True

    # Safe cancellation check must raise
    with pytest.raises(EmergencyStopTriggered):
        runner.emergency_stop.check_safe_cancellation_point()

    # Preflight is bypassed to simulate runner already inside active execution loop
    with patch("app.readiness.preflight.run_preflight") as mock_preflight:
        mock_preflight.return_value = PreflightResult(checks=[])
        exit_code = runner.start(max_iterations=1)

    # Loop observed emergency stop: transitioned to PAUSED and refused dispatch
    assert any(state == RunnerLifecycleState.PAUSED for _, state, _ in runner.lifecycle._history)
    assert exit_code == ExitCode.SUCCESS

    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert len(provider.sent_messages) == 0


def test_unified_emergency_stop_across_all_subsystems(db_session, test_setup):
    """
    4. Health, preflight, analytics, Control Plane, and WorkerDaemon all report
       the exact same Emergency Stop state.
    """
    camp, contact, msg, lock_file = test_setup
    e_stop = EmergencyStop(db_session)

    # Mock ProcessLock.read_lock_file to return None so local disk lockfile residue does not affect health
    with patch.object(ProcessLock, "read_lock_file", return_value=None):
        # A. When INACTIVE
        e_stop.resume()

        health_res = evaluate_system_health(db_session)
        assert health_res.details["emergency_stop_active"] is False

        preflight_res = check_emergency_stop_status(db_session)
        assert preflight_res.passed is True

        analytics_live = AnalyticsService.get_queue_live_analytics(db_session)
        assert analytics_live["emergency_stop_status"] == "INACTIVE"

        analytics_sys = AnalyticsService.get_system_analytics(db_session)
        assert analytics_sys["emergency_stop"]["active"] is False

        dash_snapshot = DashboardService.get_dashboard_snapshot(db_session)
        assert dash_snapshot["emergency_stop"]["is_active"] is False

        allowed, start_msg, _ = RunnerControlService.start_runner(
            db_session, campaign_id=camp.id, operator_username="admin", runner_spawner=lambda c: 99999
        )
        # Runner start is not blocked by emergency stop
        assert "Emergency Stop is currently ACTIVE" not in (start_msg or "")
        assert "Emergency stop is currently ACTIVE" not in (start_msg or "")

        # B. When ACTIVE
        e_stop.trigger(reason="Unified System Test Killswitch")

        health_res = evaluate_system_health(db_session)
        assert health_res.details["emergency_stop_active"] is True
        assert health_res.state == HealthState.STOPPED

        preflight_res = check_emergency_stop_status(db_session)
        assert preflight_res.passed is False
        assert preflight_res.exit_code == ExitCode.EMERGENCY_STOP_ACTIVE
        assert "Unified System Test Killswitch" in preflight_res.message

        analytics_live = AnalyticsService.get_queue_live_analytics(db_session)
        assert analytics_live["emergency_stop_status"] == "ACTIVE"

        analytics_sys = AnalyticsService.get_system_analytics(db_session)
        assert analytics_sys["emergency_stop"]["active"] is True
        assert analytics_sys["emergency_stop"]["reason"] == "Unified System Test Killswitch"

    dash_snapshot = DashboardService.get_dashboard_snapshot(db_session)
    assert dash_snapshot["emergency_stop"]["is_active"] is True
    assert dash_snapshot["emergency_stop"]["reason"] == "Unified System Test Killswitch"

    allowed, start_msg, _ = RunnerControlService.start_runner(db_session, campaign_id=camp.id, operator_username="admin")
    assert allowed is False
    assert "Emergency stop is currently ACTIVE" in start_msg

    # WorkerDaemon supervision check
    daemon = WorkerDaemon(poll_interval=0.01)
    daemon._is_runner_active = MagicMock(return_value=True)
    daemon._stop_supervised_runner = MagicMock()
    daemon._supervise_campaign(db_session)
    daemon._stop_supervised_runner.assert_called_once()


def test_no_stale_cached_state_survives_activation(db_session):
    """5. Proves that no stale cached Emergency Stop state can survive activation."""
    e_stop_instance = EmergencyStop(db_session)

    # Initial read caches False
    assert e_stop_instance.is_active() is False

    # External activation occurs
    external_operator = EmergencyStop(db_session)
    external_operator.trigger(reason="Urgent Operator Intervention")

    # Before TTL expires (simulated immediate tight check without time passing):
    # force_refresh=True immediately invalidates and reflects DB
    assert e_stop_instance.is_active(force_refresh=True) is True

    # After TTL expires (>= 1.0s):
    e_stop_instance._last_checked_mono = time.monotonic() - 1.1
    assert e_stop_instance.is_active() is True
