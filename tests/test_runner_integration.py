"""
Integration tests for the Single-Campaign Production Runner daemon.
"""

import time
import pytest
from unittest.mock import MagicMock
from app.cli.exit_codes import ExitCode
from app.runner.production_runner import ProductionRunner
from app.providers.mock_provider import MockMessageProvider
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.audit_log import AuditLog
from app.queue.state_machine import QueueState
from app.scheduler.emergency_stop import EmergencyStop


def test_production_runner_single_campaign_dispatch(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_test.lock")

    camp = Campaign(
        name="Runner Integration Campaign",
        message_template="Hello {{name}}",
        status="RUNNING",
        min_delay_seconds=1,
        max_delay_seconds=1,
        daily_limit=10,
    )
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Frank", phone_e164="+15559998888", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Frank",
        status=QueueState.QUEUED,
        idempotency_key="runner_test_msg_1"
    )
    db_session.add(msg)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(
        db=db_session,
        campaign_id=camp.id,
        provider=provider,
        poll_interval=0.05,
        lock_file=lock_file
    )

    # Run for 1 iteration
    exit_code = runner.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    # Verify message sent
    db_session.refresh(msg)
    assert msg.status == QueueState.SENT
    assert len(provider.sent_messages) == 1
    assert provider.is_connected is False  # Disconnected on clean exit

    # Verify lock released
    assert runner.process_lock.is_held is False


def test_production_runner_rejects_duplicate_instance(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_dup.lock")

    camp = Campaign(name="DupCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner1 = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, lock_file=lock_file)
    runner2 = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, lock_file=lock_file)

    # Manually hold lock with runner1
    assert runner1.process_lock.acquire(worker_id="r1", campaign_id=camp.id) is True

    # runner2 start must be blocked with CONCURRENCY_ERROR
    exit_code = runner2.start(max_iterations=1)
    assert exit_code == ExitCode.CONCURRENCY_ERROR

    runner1.process_lock.release()


def test_production_runner_rejects_non_running_campaign(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_draft.lock")

    camp = Campaign(name="DraftCamp", message_template="Hi", status="DRAFT")
    db_session.add(camp)
    db_session.commit()

    provider = MockMessageProvider()
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, lock_file=lock_file)

    exit_code = runner.start(max_iterations=1)
    assert exit_code == ExitCode.INVALID_STATE


def test_production_runner_emergency_stop_pauses_claims(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_estop.lock")

    camp = Campaign(name="EstopCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Grace", phone_e164="+15551112222", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Grace",
        status=QueueState.QUEUED,
        idempotency_key="estop_msg_1"
    )
    db_session.add(msg)
    db_session.commit()

    # Trigger emergency stop before running
    e_stop = EmergencyStop(db_session)
    e_stop.trigger(reason="Test stop")

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.05, lock_file=lock_file)

    exit_code = runner.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    # Message must NOT have been sent!
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert len(provider.sent_messages) == 0

    e_stop.resume("Test resumed")


def test_production_runner_circuit_breaker_pause(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_cb.lock")

    camp = Campaign(
        name="CBCamp",
        message_template="Hi",
        status="RUNNING",
        min_delay_seconds=1,
        max_delay_seconds=1,
        error_threshold=1  # Trip on 1 failure
    )
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Hank", phone_e164="+15553334444", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg1 = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Hank 1",
        status=QueueState.QUEUED,
        idempotency_key="cb_msg_1"
    )
    msg2 = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Hank 2",
        status=QueueState.QUEUED,
        idempotency_key="cb_msg_2"
    )
    db_session.add_all([msg1, msg2])
    db_session.commit()

    # Provider that fails permanent
    provider = MockMessageProvider(default_success=False, default_temporary_error=False)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.01, lock_file=lock_file)

    # 2 iterations: first message fails, trips CB to PAUSED, second iteration pauses
    exit_code = runner.start(max_iterations=2)
    assert exit_code == ExitCode.SUCCESS

    db_session.refresh(camp)
    assert camp.status == "PAUSED"  # Campaign paused by CircuitBreaker

    # msg1 failed, msg2 must remain QUEUED
    db_session.refresh(msg1)
    db_session.refresh(msg2)
    assert msg1.status == QueueState.FAILED
    assert msg2.status == QueueState.QUEUED


def test_production_runner_unknown_outcome_handling(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_uo.lock")

    camp = Campaign(
        name="UOCamp",
        message_template="Hi",
        status="RUNNING",
        min_delay_seconds=1,
        max_delay_seconds=1
    )
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Ian", phone_e164="+15554445555", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Ian",
        status=QueueState.QUEUED,
        idempotency_key="uo_runner_msg_1"
    )
    db_session.add(msg)
    db_session.commit()

    # Provider returning UNKNOWN_OUTCOME
    from app.providers.base import SendResult

    mock_prov = MagicMock()
    mock_prov.health_check.return_value = True
    mock_prov.send_message.return_value = SendResult(
        success=False,
        error_message="[UNKNOWN_OUTCOME] Page crashed while awaiting confirmation checkmark",
        is_temporary_error=False,
        raw_response={"category": "UNKNOWN_OUTCOME", "details": "Browser crash"}
    )

    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=mock_prov, poll_interval=1, lock_file=lock_file)
    exit_code = runner.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    db_session.refresh(msg)
    # Must be marked FAILED with UNKNOWN_OUTCOME, NOT retry pending!
    assert msg.status == QueueState.FAILED
    assert msg.error_type == "UNKNOWN_OUTCOME"
    assert msg.next_retry_at is None  # Blind retry blocked!


def test_production_runner_provider_connect_failure(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_prov_conn.lock")
    camp = Campaign(name="ProvConnCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    mock_prov = MagicMock()
    mock_prov.connect.side_effect = RuntimeError("Could not open browser")

    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=mock_prov, lock_file=lock_file)
    assert runner.start(max_iterations=1) == ExitCode.PROVIDER_UNAVAILABLE


def test_production_runner_provider_unhealthy(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_prov_unhealthy.lock")
    camp = Campaign(name="ProvUnhealthyCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    mock_prov = MagicMock()
    mock_prov.health_check.return_value = False

    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=mock_prov, lock_file=lock_file)
    assert runner.start(max_iterations=1) == ExitCode.AUTHENTICATION_REQUIRED


def test_production_runner_campaign_terminal_breaks_loop(db_session, tmp_path):
    from unittest.mock import patch
    lock_file = str(tmp_path / "runner_term.lock")
    camp = Campaign(name="TerminalCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.01, lock_file=lock_file)

    def set_camp_completed(*args, **kwargs):
        camp.status = "COMPLETED"
        db_session.commit()
        return None

    with patch("app.scheduler.worker.QueueWorker.process_next_message", side_effect=set_camp_completed):
        code = runner.start(max_iterations=5)
        assert code == ExitCode.SUCCESS


def test_production_runner_unhandled_exception(db_session, tmp_path):
    from unittest.mock import patch
    lock_file = str(tmp_path / "runner_exc.lock")
    camp = Campaign(name="ExcCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.01, lock_file=lock_file)

    with patch.object(runner.emergency_stop, "is_active", side_effect=RuntimeError("DB exploded")):
        code = runner.start(max_iterations=1)
        assert code == ExitCode.GENERAL_ERROR


def test_signal_coordinator_edge_cases():
    import signal
    from app.runner.signals import SignalCoordinator

    coord = SignalCoordinator()

    # Callback raising exception
    def bad_callback():
        raise ValueError("callback failed")

    coord.register_callback(bad_callback)
    coord.request_shutdown()
    assert coord.shutdown_requested is True

    # Register and restore handlers
    coord2 = SignalCoordinator()
    coord2.register_handlers()
    coord2._handle_signal(signal.SIGINT, None)
    assert coord2.shutdown_requested is True
    coord2.restore_handlers()


def test_production_runner_circuit_breaker_tripped(db_session, tmp_path):
    from app.runner.lifecycle import RunnerLifecycleState
    lock_file = str(tmp_path / "runner_cb.lock")
    camp = Campaign(name="CBCamp", message_template="Hi", status="RUNNING", error_threshold=3)
    db_session.add(camp)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.01, lock_file=lock_file)

    # Pre-set consecutive errors so the loop trips circuit breaker
    runner.circuit_breaker._consecutive_errors[camp.id] = 5

    # Request shutdown so it exits the loop cleanly after pausing
    runner.signals.request_shutdown()

    code = runner.start(max_iterations=1)
    assert code == ExitCode.SUCCESS


def test_production_runner_idle_queue(db_session, tmp_path):
    lock_file = str(tmp_path / "runner_idle.lock")
    camp = Campaign(name="IdleCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.01, lock_file=lock_file)

    # No messages queued -> queue is idle
    code = runner.start(max_iterations=1)
    assert code == ExitCode.SUCCESS


def test_production_runner_recovers_stale_leases(db_session, tmp_path):
    from datetime import datetime, timezone, timedelta
    lock_file = str(tmp_path / "runner_stale.lock")
    camp = Campaign(name="StaleCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="StaleContact", phone_e164="+15551112222", country_code="US")
    db_session.add(contact)
    db_session.flush()

    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Stale msg",
        status=QueueState.PROCESSING,
        locked_by="dead_worker",
        locked_at=stale_time,
        idempotency_key="stale_lease_msg_1"
    )
    db_session.add(msg)
    db_session.commit()

    provider = MockMessageProvider(default_success=True)
    runner = ProductionRunner(db=db_session, campaign_id=camp.id, provider=provider, poll_interval=0.01, lock_file=lock_file)

    code = runner.start(max_iterations=1)
    assert code == ExitCode.SUCCESS

    db_session.refresh(msg)
    # The stale lease should have been recovered (and then dispatched or retry pending)
    assert msg.status in (QueueState.SENT, QueueState.QUEUED, QueueState.RETRY_PENDING)
