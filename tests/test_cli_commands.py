"""
Unit and functional tests for CLI command handlers.
"""

import json
import pytest
from unittest.mock import MagicMock
from argparse import Namespace

from app.cli.exit_codes import ExitCode
from app.cli.commands.session import handle_session_login, handle_session_status, handle_session_logout
from app.cli.commands.campaign import (
    handle_campaign_run, handle_campaign_status,
    handle_campaign_pause, handle_campaign_resume, handle_campaign_stop
)
from app.cli.commands.queue import (
    handle_queue_status, handle_queue_inspect,
    handle_queue_reconcile, handle_queue_override
)
from app.cli.commands.emergency import (
    handle_emergency_stop, handle_emergency_status, handle_emergency_resume
)
from app.cli.commands.runner import handle_runner_status, handle_runner_start, handle_runner_stop
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.audit_log import AuditLog
from app.scheduler.emergency_stop import EmergencyStop


# ─── 1. Session Commands Tests ───────────────────────────────────────────────

def test_session_login_already_connected(db_session):
    mock_mgr = MagicMock()
    mock_mgr.initialize_session.return_value = "CONNECTED"

    args = Namespace(timeout=10, headless=True)
    code = handle_session_login(args, db_session, session_manager=mock_mgr)
    assert code == ExitCode.SUCCESS


def test_session_login_qr_timeout(db_session):
    mock_mgr = MagicMock()
    mock_mgr.initialize_session.return_value = "AUTHENTICATING"
    mock_mgr.await_authentication.return_value = False

    args = Namespace(timeout=5, headless=True)
    code = handle_session_login(args, db_session, session_manager=mock_mgr)
    assert code == ExitCode.AUTHENTICATION_REQUIRED


def test_session_status_and_logout(db_session, tmp_path):
    mock_mgr = MagicMock()
    mock_mgr.state = "CONNECTED"
    mock_mgr.check_health.return_value = True

    # Status
    args = Namespace()
    code = handle_session_status(args, db_session, session_manager=mock_mgr)
    assert code == ExitCode.SUCCESS

    # Logout
    logout_args = Namespace(clear_cache=False)
    logout_code = handle_session_logout(logout_args, db_session, session_manager=mock_mgr)
    assert logout_code == ExitCode.SUCCESS
    assert mock_mgr.shutdown.called


# ─── 2. Campaign Commands Tests ──────────────────────────────────────────────

def test_campaign_state_transition_only(db_session):
    camp = Campaign(
        name="Business Campaign",
        message_template="Hello {{name}}",
        status="DRAFT"
    )
    db_session.add(camp)
    db_session.commit()

    # campaign run strictly transitions state, does NOT start runner
    args = Namespace(campaign_id=camp.id)
    code = handle_campaign_run(args, db_session)
    assert code == ExitCode.SUCCESS

    db_session.refresh(camp)
    assert camp.status == "RUNNING"

    # Pause
    pause_code = handle_campaign_pause(args, db_session)
    assert pause_code == ExitCode.SUCCESS
    db_session.refresh(camp)
    assert camp.status == "PAUSED"

    # Resume
    resume_code = handle_campaign_resume(args, db_session)
    assert resume_code == ExitCode.SUCCESS
    db_session.refresh(camp)
    assert camp.status == "RUNNING"

    # Status
    status_code = handle_campaign_status(args, db_session)
    assert status_code == ExitCode.SUCCESS

    # Stop
    stop_args = Namespace(campaign_id=camp.id, confirm=True)
    stop_code = handle_campaign_stop(stop_args, db_session)
    assert stop_code == ExitCode.SUCCESS
    db_session.refresh(camp)
    assert camp.status == "CANCELLED"


def test_campaign_not_found(db_session):
    args = Namespace(campaign_id=99999)
    assert handle_campaign_run(args, db_session) == ExitCode.NOT_FOUND
    assert handle_campaign_status(args, db_session) == ExitCode.NOT_FOUND
    assert handle_campaign_pause(args, db_session) == ExitCode.NOT_FOUND


# ─── 3. Queue Commands & UNKNOWN_OUTCOME Override Tests ───────────────────────

def test_queue_status_and_inspect(db_session):
    camp = Campaign(name="QCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Jane", phone_e164="+15550001111", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Jane",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        idempotency_key="uo_msg_1",
        last_error="[UNKNOWN_OUTCOME] Page crashed after send click"
    )
    db_session.add(msg)
    db_session.commit()

    # Queue status
    status_args = Namespace(campaign_id=camp.id)
    assert handle_queue_status(status_args, db_session) == ExitCode.SUCCESS

    # Queue inspect
    inspect_args = Namespace(message_id=msg.id)
    assert handle_queue_inspect(inspect_args, db_session) == ExitCode.SUCCESS

    # Queue reconcile
    rec_args = Namespace(campaign_id=camp.id)
    assert handle_queue_reconcile(rec_args, db_session) == ExitCode.SUCCESS


def test_queue_override_requires_reason(db_session):
    camp = Campaign(name="QCamp2", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()
    contact = Contact(name="Bob", phone_e164="+15550002222", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Bob",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        idempotency_key="uo_msg_2"
    )
    db_session.add(msg)
    db_session.commit()

    # Attempt override with empty reason -> INVALID_ARGUMENT
    args_no_reason = Namespace(message_id=msg.id, reason="", operator="test_op")
    assert handle_queue_override(args_no_reason, db_session) == ExitCode.INVALID_ARGUMENT


def test_queue_override_rejects_non_unknown_outcome(db_session):
    camp = Campaign(name="QCamp3", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()
    contact = Contact(name="Alice", phone_e164="+15550003333", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Alice",
        status="FAILED",
        error_type="PERMANENT",
        idempotency_key="uo_msg_3"
    )
    db_session.add(msg)
    db_session.commit()

    args = Namespace(message_id=msg.id, reason="Some reason", operator="test_op")
    assert handle_queue_override(args, db_session) == ExitCode.INVALID_STATE


def test_queue_override_confirmation_string_mismatch(db_session):
    camp = Campaign(name="QCamp4", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()
    contact = Contact(name="Dan", phone_e164="+15550004444", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Dan",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        idempotency_key="uo_msg_4"
    )
    db_session.add(msg)
    db_session.commit()

    args = Namespace(message_id=msg.id, reason="Checked chat in WhatsApp Web", operator="test_op")
    # Wrong confirmation string
    code = handle_queue_override(args, db_session, confirmation_input="yes")
    assert code == ExitCode.UNKNOWN_OUTCOME_BLOCKED

    db_session.refresh(msg)
    assert msg.status == "FAILED"  # NOT changed


def test_queue_override_success_with_audit_record(db_session):
    camp = Campaign(name="QCamp5", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()
    contact = Contact(name="Eve", phone_e164="+15550005555", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Eve",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        idempotency_key="uo_msg_5"
    )
    db_session.add(msg)
    db_session.commit()

    args = Namespace(
        message_id=msg.id,
        reason="Manually inspected WhatsApp Web chat; contact never received outreach message.",
        operator="lead_operator"
    )

    code = handle_queue_override(args, db_session, confirmation_input="CONFIRM-NOT-DELIVERED")
    assert code == ExitCode.SUCCESS

    # Verify message transitioned to QUEUED
    db_session.refresh(msg)
    assert msg.status == "QUEUED"
    assert "[MANUAL_OVERRIDE]" in msg.last_error

    # Verify comprehensive AuditLog entry
    audit = db_session.query(AuditLog).filter(
        AuditLog.event_type == "MANUAL_RECONCILIATION_OVERRIDE",
        AuditLog.message_id == msg.id
    ).first()
    assert audit is not None
    assert audit.status == "QUEUED"

    audit_payload = json.loads(audit.error_message)
    assert audit_payload["previous_state"] == "FAILED"
    assert audit_payload["new_state"] == "QUEUED"
    assert audit_payload["manual_verification_confirmation"] is True
    assert "Manually inspected WhatsApp Web" in audit_payload["explicit_override_reason"]
    assert audit_payload["operator_identity"] == "lead_operator"


# ─── 4. Emergency Stop Commands Tests ────────────────────────────────────────

def test_emergency_stop_lifecycle(db_session):
    # Status when inactive
    args = Namespace()
    assert handle_emergency_status(args, db_session) == ExitCode.SUCCESS

    # Trigger stop
    stop_args = Namespace(reason="Operational halt test")
    assert handle_emergency_stop(stop_args, db_session) == ExitCode.SUCCESS

    controller = EmergencyStop(db_session)
    assert controller.is_active() is True

    # Resume stop
    res_args = Namespace(reason="Test cleared")
    assert handle_emergency_resume(res_args, db_session) == ExitCode.SUCCESS
    assert EmergencyStop(db_session).is_active() is False


# ─── 5. Runner Status Command Tests ──────────────────────────────────────────

def test_runner_status_no_active_runner(db_session):
    args = Namespace()
    assert handle_runner_status(args, db_session) == ExitCode.SUCCESS


def test_session_login_qr_success(db_session):
    mock_mgr = MagicMock()
    mock_mgr.initialize_session.return_value = "AUTHENTICATING"
    mock_mgr.await_authentication.return_value = True

    args = Namespace(timeout=10, headless=True)
    code = handle_session_login(args, db_session, session_manager=mock_mgr)
    assert code == ExitCode.SUCCESS


def test_session_login_exception(db_session):
    mock_mgr = MagicMock()
    mock_mgr.initialize_session.side_effect = RuntimeError("Fatal browser error")

    args = Namespace(timeout=10, headless=True)
    code = handle_session_login(args, db_session, session_manager=mock_mgr)
    assert code == ExitCode.PROVIDER_UNAVAILABLE


def test_session_logout_clear_cache(db_session, tmp_path):
    from unittest.mock import patch
    args = Namespace(clear_cache=True)
    with patch("os.path.exists", return_value=True), patch("shutil.rmtree") as mock_rm:
        code = handle_session_logout(args, db_session)
        assert code == ExitCode.SUCCESS
        assert mock_rm.called


def test_campaign_run_already_running(db_session):
    camp = Campaign(name="AlreadyRunningCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)
    assert handle_campaign_run(args, db_session) == ExitCode.SUCCESS


def test_campaign_run_invalid_transition(db_session):
    camp = Campaign(name="CompletedCamp", message_template="Hi", status="COMPLETED")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)
    assert handle_campaign_run(args, db_session) == ExitCode.INVALID_STATE


def test_campaign_pause_already_paused(db_session):
    camp = Campaign(name="AlreadyPausedCamp", message_template="Hi", status="PAUSED")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)
    assert handle_campaign_pause(args, db_session) == ExitCode.SUCCESS


def test_campaign_pause_not_found(db_session):
    args = Namespace(campaign_id=99999)
    assert handle_campaign_pause(args, db_session) == ExitCode.NOT_FOUND


def test_campaign_resume_already_running(db_session):
    camp = Campaign(name="ResumeRunningCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)
    assert handle_campaign_resume(args, db_session) == ExitCode.SUCCESS


def test_campaign_resume_not_found(db_session):
    args = Namespace(campaign_id=99999)
    assert handle_campaign_resume(args, db_session) == ExitCode.NOT_FOUND


def test_campaign_stop_not_found(db_session):
    args = Namespace(campaign_id=99999, confirm=True)
    assert handle_campaign_stop(args, db_session) == ExitCode.NOT_FOUND


def test_runner_start_missing_campaign_id(db_session):
    args = Namespace(campaign_id=None)
    assert handle_runner_start(args, db_session) == ExitCode.INVALID_ARGUMENT


def test_runner_start_campaign_not_found(db_session):
    args = Namespace(campaign_id=99999)
    assert handle_runner_start(args, db_session) == ExitCode.NOT_FOUND


def test_runner_start_campaign_not_running(db_session):
    camp = Campaign(name="DraftCampForRunner", message_template="Hi", status="DRAFT")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)
    assert handle_runner_start(args, db_session) == ExitCode.INVALID_STATE


def test_runner_start_exit_codes_handling(db_session):
    from unittest.mock import patch
    camp = Campaign(name="RunnerExitTestCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)

    with patch("app.runner.production_runner.ProductionRunner.start", return_value=ExitCode.CONCURRENCY_ERROR):
        assert handle_runner_start(args, db_session) == ExitCode.CONCURRENCY_ERROR

    with patch("app.runner.production_runner.ProductionRunner.start", return_value=ExitCode.AUTHENTICATION_REQUIRED):
        assert handle_runner_start(args, db_session) == ExitCode.AUTHENTICATION_REQUIRED

    with patch("app.runner.production_runner.ProductionRunner.start", return_value=ExitCode.PROVIDER_UNAVAILABLE):
        assert handle_runner_start(args, db_session) == ExitCode.PROVIDER_UNAVAILABLE

    with patch("app.runner.production_runner.ProductionRunner.start", return_value=ExitCode.SUCCESS):
        assert handle_runner_start(args, db_session) == ExitCode.SUCCESS


def test_runner_status_with_active_runner(db_session):
    from unittest.mock import patch
    args = Namespace()
    mock_info = {
        "pid": 5432,
        "worker_id": "worker_status_test",
        "campaign_id": 10,
        "started_at": "2026-09-11T12:00:00Z",
        "last_heartbeat": "2026-09-11T12:05:00Z",
    }
    with patch("app.runner.process_lock.ProcessLock.get_active_runner_info", return_value=mock_info):
        assert handle_runner_status(args, db_session) == ExitCode.SUCCESS


def test_runner_stop_active_runner(db_session):
    from unittest.mock import patch
    args = Namespace()
    mock_info = {"pid": 7777, "worker_id": "w_test"}

    with patch("app.runner.process_lock.ProcessLock.get_active_runner_info", return_value=mock_info), \
         patch("os.kill") as mock_kill, \
         patch("app.cli.commands.runner.is_pid_alive", return_value=False), \
         patch("app.runner.process_lock.ProcessLock.release"):
        code = handle_runner_stop(args, db_session)
        assert code == ExitCode.SUCCESS
        assert mock_kill.called


def test_runner_stop_timeout_warning(db_session):
    from unittest.mock import patch
    args = Namespace()
    mock_info = {"pid": 7777, "worker_id": "w_test"}

    with patch("app.runner.process_lock.ProcessLock.get_active_runner_info", return_value=mock_info), \
         patch("os.kill"), \
         patch("app.cli.commands.runner.is_pid_alive", return_value=True), \
         patch("time.sleep"):
        code = handle_runner_stop(args, db_session)
        assert code == ExitCode.SUCCESS


def test_runner_stop_process_lookup_error(db_session):
    from unittest.mock import patch
    args = Namespace()
    mock_info = {"pid": 7777, "worker_id": "w_test"}

    with patch("app.runner.process_lock.ProcessLock.get_active_runner_info", return_value=mock_info), \
         patch("os.kill", side_effect=ProcessLookupError), \
         patch("app.runner.process_lock.ProcessLock.release"):
        code = handle_runner_stop(args, db_session)
        assert code == ExitCode.SUCCESS


def test_runner_stop_general_exception(db_session):
    from unittest.mock import patch
    args = Namespace()
    mock_info = {"pid": 7777, "worker_id": "w_test"}

    with patch("app.runner.process_lock.ProcessLock.get_active_runner_info", return_value=mock_info), \
         patch("os.kill", side_effect=RuntimeError("Kill error")):
        code = handle_runner_stop(args, db_session)
        assert code == ExitCode.GENERAL_ERROR


def test_session_login_default_session_manager(db_session):
    from unittest.mock import patch
    args = Namespace(timeout=5, headless=True)
    with patch("app.providers.whatsapp_web.browser.WhatsAppBrowser") as mock_b_cls, \
         patch("app.providers.whatsapp_web.session_manager.WhatsAppSessionManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.initialize_session.return_value = "CONNECTED"
        code = handle_session_login(args, db_session, session_manager=None)
        assert code == ExitCode.SUCCESS
        assert mock_b_cls.called
        assert mock_sm_cls.called


def test_campaign_run_general_exception(db_session):
    from unittest.mock import patch
    camp = Campaign(name="ExcCamp2", message_template="Hi", status="DRAFT")
    db_session.add(camp)
    db_session.commit()

    args = Namespace(campaign_id=camp.id)
    with patch("app.campaigns.manager.CampaignManager.transition_state", side_effect=RuntimeError("DB error")):
        code = handle_campaign_run(args, db_session)
        assert code == ExitCode.GENERAL_ERROR


def test_campaign_stop_invalid_state(db_session):
    camp = Campaign(name="StopInvalidCamp", message_template="Hi", status="DRAFT")
    db_session.add(camp)
    db_session.commit()

    # DRAFT cannot transition to CANCELLED in manager.transition_state
    args = Namespace(campaign_id=camp.id)
    code = handle_campaign_stop(args, db_session)
    assert code == ExitCode.INVALID_STATE


