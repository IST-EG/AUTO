"""
Unit tests for WhatsAppCommandService and Worker-side WhatsAppCommandHandler.

Verifies:
- Deterministic command delivery: REQUESTED -> CLAIMED -> EXECUTING -> COMPLETED/FAILED
- Single-inflight command serialization (rejection of concurrent commands)
- Atomic CAS claim with monotonic versioning and lease deadline
- Duplicate polling prevention
- Lease expiration and orphan crash recovery
- Worker-side execution of HEALTH_CHECK, RECONNECT, DISCONNECT, LOGOUT
"""

import json
import time
from datetime import datetime, timezone, timedelta
import pytest

from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.services.whatsapp_command_service import WhatsAppCommandService
from app.runner.whatsapp_command_handler import WhatsAppCommandHandler


def test_submit_command_success(web_session):
    """Verifies that submit_command creates a valid active command slot."""
    success, message, cmd = WhatsAppCommandService.submit_command(
        db=web_session,
        action="HEALTH_CHECK",
        requested_by="admin_user",
        requested_by_id="usr_123",
    )

    assert success is True
    assert "successfully" in message
    assert cmd is not None
    assert cmd["action"] == "HEALTH_CHECK"
    assert cmd["status"] == "REQUESTED"
    assert cmd["version"] == 1
    assert cmd["requested_by"] == "admin_user"
    assert cmd["request_id"].startswith("req_wa_")

    # Verify AppSetting was updated
    active = WhatsAppCommandService.get_active_command(web_session)
    assert active is not None
    assert active["request_id"] == cmd["request_id"]

    # Verify AuditLog was emitted
    audit = (
        web_session.query(AuditLog)
        .filter(AuditLog.event_type == "WHATSAPP_HEALTH_CHECK_REQUESTED")
        .first()
    )
    assert audit is not None
    assert audit.status == "REQUESTED"


def test_submit_command_single_inflight_conflict(web_session):
    """Verifies that submitting a second command while one is in progress fails with conflict."""
    # Submit first command
    success1, msg1, cmd1 = WhatsAppCommandService.submit_command(
        db=web_session,
        action="HEALTH_CHECK",
        requested_by="operator1",
    )
    assert success1 is True

    # Attempt second command immediately
    success2, msg2, cmd2 = WhatsAppCommandService.submit_command(
        db=web_session,
        action="RECONNECT",
        requested_by="operator2",
    )

    assert success2 is False
    assert "currently in progress" in msg2
    assert cmd2["request_id"] == cmd1["request_id"]

    # Ensure command 1 was NOT overwritten
    active = WhatsAppCommandService.get_active_command(web_session)
    assert active["action"] == "HEALTH_CHECK"
    assert active["request_id"] == cmd1["request_id"]


def test_atomic_cas_claim_and_versioning(web_session):
    """Verifies atomic CAS claim sets status=CLAIMED, increments version, and sets 60s lease."""
    WhatsAppCommandService.submit_command(
        db=web_session,
        action="RECONNECT",
        requested_by="admin_user",
    )

    # First claim by worker
    claimed = WhatsAppCommandService.claim_command(
        db=web_session,
        worker_id="worker_vps_1",
        lease_duration_seconds=60,
    )
    assert claimed is not None
    assert claimed["status"] == "CLAIMED"
    assert claimed["claimed_by"] == "worker_vps_1"
    assert claimed["version"] == 2
    assert claimed["lease_expires_at"] is not None

    # Verify lease is ~60s in the future
    lease_dt = datetime.fromisoformat(claimed["lease_expires_at"].replace("Z", "+00:00"))
    now_utc = datetime.now(timezone.utc)
    diff = (lease_dt - now_utc).total_seconds()
    assert 55 <= diff <= 65

    # Second claim by another polling pass must return None (double execution prevented)
    second_claim = WhatsAppCommandService.claim_command(
        db=web_session,
        worker_id="worker_vps_1",
    )
    assert second_claim is None


def test_command_lifecycle_to_completion(web_session):
    """Verifies full lifecycle: REQUESTED -> CLAIMED -> EXECUTING -> COMPLETED."""
    _, _, cmd = WhatsAppCommandService.submit_command(
        db=web_session,
        action="DISCONNECT",
        requested_by="operator_test",
    )
    req_id = cmd["request_id"]

    # 1. Claim
    claimed = WhatsAppCommandService.claim_command(web_session, worker_id="worker_1")
    assert claimed["status"] == "CLAIMED"

    # 2. Mark Executing
    exec_ok = WhatsAppCommandService.mark_executing(web_session, req_id, worker_id="worker_1")
    assert exec_ok is True
    active = WhatsAppCommandService.get_active_command(web_session)
    assert active["status"] == "EXECUTING"
    assert active["executed_at"] is not None

    # 3. Complete
    comp_ok = WhatsAppCommandService.complete_command(
        db=web_session,
        request_id=req_id,
        worker_id="worker_1",
        result="Disconnected cleanly",
    )
    assert comp_ok is True

    # Verify last_completed has the terminal outcome
    last_comp = WhatsAppCommandService.get_last_completed_command(web_session)
    assert last_comp is not None
    assert last_comp["request_id"] == req_id
    assert last_comp["status"] == "COMPLETED"
    assert last_comp["result"] == "Disconnected cleanly"

    # Verify active slot is terminal
    active_now = WhatsAppCommandService.get_active_command(web_session)
    assert active_now["status"] == "COMPLETED"

    # Verify audit log
    audit = (
        web_session.query(AuditLog)
        .filter(AuditLog.event_type == "WHATSAPP_DISCONNECT_COMPLETED")
        .first()
    )
    assert audit is not None
    assert audit.status == "SUCCESS"

    # Now a new command can be submitted cleanly without conflict
    success_next, _, _ = WhatsAppCommandService.submit_command(
        db=web_session,
        action="HEALTH_CHECK",
        requested_by="operator_test",
    )
    assert success_next is True


def test_command_lifecycle_to_failure(web_session):
    """Verifies failure lifecycle: REQUESTED -> CLAIMED -> FAILED."""
    _, _, cmd = WhatsAppCommandService.submit_command(
        db=web_session,
        action="RECONNECT",
        requested_by="operator_test",
    )
    req_id = cmd["request_id"]

    WhatsAppCommandService.claim_command(web_session, worker_id="worker_1")
    WhatsAppCommandService.fail_command(
        db=web_session,
        request_id=req_id,
        worker_id="worker_1",
        error_message="Browser crash during reconnect",
    )

    last_comp = WhatsAppCommandService.get_last_completed_command(web_session)
    assert last_comp["status"] == "FAILED"
    assert last_comp["error_message"] == "Browser crash during reconnect"

    audit = (
        web_session.query(AuditLog)
        .filter(AuditLog.event_type == "WHATSAPP_COMMAND_FAILED")
        .first()
    )
    assert audit is not None
    assert audit.status == "FAILED"


def test_lease_expiration_and_orphan_recovery(web_session):
    """Verifies that an expired claimed command is cleanly auto-recovered as FAILED."""
    # Submit command
    _, _, cmd = WhatsAppCommandService.submit_command(web_session, action="HEALTH_CHECK", requested_by="user1")
    req_id = cmd["request_id"]

    # Worker claims but then crashes/times out
    WhatsAppCommandService.claim_command(web_session, worker_id="crashed_worker", lease_duration_seconds=60)

    # Manually simulate expired lease in DB
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    active = WhatsAppCommandService.get_active_command(web_session)
    active["lease_expires_at"] = past_time
    setting_row = web_session.query(AppSetting).filter(AppSetting.key == WhatsAppCommandService.ACTIVE_COMMAND_KEY).first()
    setting_row.value = json.dumps(active)
    web_session.commit()

    # Recovery pass
    recovered = WhatsAppCommandService.recover_stale_or_orphaned_command(web_session, worker_id="new_runner")
    assert recovered is not None
    assert recovered["request_id"] == req_id
    assert recovered["status"] == "FAILED"
    assert "Orphaned command recovered" in recovered["error_message"]

    # Verify audit log
    audit = (
        web_session.query(AuditLog)
        .filter(AuditLog.status == "ORPHAN_RECOVERED")
        .first()
    )
    assert audit is not None

    # Active slot is now freed for new commands
    new_sub, _, _ = WhatsAppCommandService.submit_command(web_session, action="RECONNECT", requested_by="user2")
    assert new_sub is True


def test_clear_stale_command_admin_override(web_session):
    """Verifies that an admin can manually clear a stuck command."""
    WhatsAppCommandService.submit_command(web_session, action="HEALTH_CHECK", requested_by="user1")

    cleared, msg = WhatsAppCommandService.clear_stale_command(web_session, operator_username="admin_super")
    assert cleared is True
    assert "has been cleared" in msg

    audit = web_session.query(AuditLog).filter(AuditLog.event_type == "WHATSAPP_COMMAND_CLEARED").first()
    assert audit is not None
    assert audit.status == "CLEARED"


def test_worker_command_handler_execution(web_session):
    """Verifies that WhatsAppCommandHandler polls, executes, and updates telemetry."""
    # Mock Provider
    class MockProvider:
        def __init__(self):
            self.healthy = True
            self.reconnected = True
            self.disconnected = False

        def health_check(self):
            return self.healthy

        def disconnect(self):
            self.disconnected = True

    mock_provider = MockProvider()
    handler = WhatsAppCommandHandler(db=web_session, worker_id="test_worker_1", provider=mock_provider)

    # 1. Submit command
    WhatsAppCommandService.submit_command(web_session, action="HEALTH_CHECK", requested_by="operator1")

    # 2. Worker polls and executes
    executed = handler.poll_and_execute()
    assert executed is not None
    assert executed["action"] == "HEALTH_CHECK"

    # Verify command concluded
    last_cmd = WhatsAppCommandService.get_last_completed_command(web_session)
    assert last_cmd["status"] == "COMPLETED"
    assert "HEALTHY" in last_cmd["result"]

    # Verify telemetry was published
    telemetry_row = web_session.query(AppSetting).filter(AppSetting.key == "system:whatsapp_telemetry").first()
    assert telemetry_row is not None
    telemetry = json.loads(telemetry_row.value)
    assert telemetry["worker_id"] == "test_worker_1"
    assert telemetry["state"] == "CONNECTED"
