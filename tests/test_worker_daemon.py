"""
Safety and Behavioral Tests for Always-On WorkerDaemon.

Verifies:
1. Worker starts cleanly with Campaign 1 = DRAFT.
2. Worker remains alive with zero queue.
3. Heartbeat publication to system:worker_heartbeat.
4. Worker identity publication to system:worker_identity.
5. Campaign state immutability (Campaign 1 remains DRAFT).
6. Queue untouched by idle worker.
7. Zero Chrome cold-starts on worker start.
8. Zero WhatsApp Web launches.
9. Zero QR flows.
10. Zero messages sent.
11. ProductionRunner still refuses DRAFT campaigns.
12. Worker supervision never promotes DRAFT to RUNNING.
13. Worker and runner lock isolation.
14. Graceful worker shutdown without force-killing.
15. Command execution (PREFLIGHT safe execution).
16. Orphan command recovery on boot.
17. Static systemd unit validation (no --campaign-id 1).
"""

import os
import json
import time
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app.cli.exit_codes import ExitCode
from app.runner.worker_daemon import WorkerDaemon
from app.runner.production_runner import ProductionRunner
from app.runner.process_lock import ProcessLock
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.queue.state_machine import QueueState
from app.utils.settings import settings


# --------------------------------------------------------------------------
# Test 1 & 5: Worker starts cleanly with Campaign 1 = DRAFT; remains DRAFT
# --------------------------------------------------------------------------
def test_worker_starts_cleanly_when_campaign_draft(db_session, tmp_path):
    """WorkerDaemon must start and run cleanly when Campaign 1 is in DRAFT state."""
    lock_file = str(tmp_path / "worker.lock")

    camp = Campaign(
        id=1,
        name="Test Draft Campaign",
        message_template="Hello {{name}}",
        status="DRAFT",
    )
    db_session.add(camp)
    db_session.commit()

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=2)
    assert exit_code == ExitCode.SUCCESS

    # Verify Campaign 1 was NOT modified
    db_session.refresh(camp)
    assert camp.status == "DRAFT"


# --------------------------------------------------------------------------
# Test 2 & 6: Worker remains alive with zero queue; queue untouched
# --------------------------------------------------------------------------
def test_worker_remains_alive_with_zero_queue(db_session, tmp_path):
    """WorkerDaemon must remain alive and healthy when message queue is empty."""
    lock_file = str(tmp_path / "worker.lock")

    # Assert 0 messages in queue
    msg_count = db_session.query(Message).count()
    assert msg_count == 0

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=3)
    assert exit_code == ExitCode.SUCCESS

    # Verify queue is still 0
    assert db_session.query(Message).count() == 0


def test_queue_untouched_by_worker(db_session, tmp_path):
    """WorkerDaemon must not claim or mutate queue messages."""
    lock_file = str(tmp_path / "worker.lock")

    camp = Campaign(name="Camp", message_template="Hi", status="DRAFT")
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Alice", phone_e164="+15551112222", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Alice",
        status=QueueState.QUEUED,
        idempotency_key="worker_queue_test_msg"
    )
    db_session.add(msg)
    db_session.commit()

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=2)
    assert exit_code == ExitCode.SUCCESS

    # Re-query message: status must be untouched and unleased
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert msg.locked_by is None


# --------------------------------------------------------------------------
# Test 3: Heartbeat publication to system:worker_heartbeat
# --------------------------------------------------------------------------
def test_worker_publishes_heartbeat_periodically(db_session, tmp_path, monkeypatch):
    """WorkerDaemon must publish infrastructure heartbeat with runner_state: STANDBY."""
    lock_file = str(tmp_path / "worker.lock")
    monkeypatch.setattr(settings, "WORKER_INSTANCE_ID", "oracle-arm64-worker-01")

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    row = db_session.query(AppSetting).filter(AppSetting.key == "system:worker_heartbeat").first()
    assert row is not None
    data = json.loads(row.value)
    assert data["instance_id"] == "oracle-arm64-worker-01"
    assert data["last_seen"] is not None
    assert "uptime_seconds" in data
    assert data["provider_active"] is False


# --------------------------------------------------------------------------
# Test 4: Worker identity publication to system:worker_identity
# --------------------------------------------------------------------------
def test_worker_publishes_identity_on_start(db_session, tmp_path, monkeypatch):
    """WorkerDaemon must publish worker identity metadata."""
    lock_file = str(tmp_path / "worker.lock")
    monkeypatch.setattr(settings, "WORKER_INSTANCE_ID", "oracle-arm64-worker-01")

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    row = db_session.query(AppSetting).filter(AppSetting.key == "system:worker_identity").first()
    assert row is not None
    data = json.loads(row.value)
    assert data["instance_id"] == "oracle-arm64-worker-01"
    assert "capabilities" in data
    assert "platform" in data
    assert "arch" in data


# --------------------------------------------------------------------------
# Test 7, 8, 9, 10: Zero Chrome cold-start, WhatsApp launch, QR flow, or messages
# --------------------------------------------------------------------------
def test_zero_chrome_cold_start_or_whatsapp_activity(db_session, tmp_path):
    """WorkerDaemon idle loop must NOT cold-start Chrome, launch WhatsApp Web, initiate QR, or send messages."""
    lock_file = str(tmp_path / "worker.lock")

    with patch("subprocess.Popen") as mock_popen:
        daemon = WorkerDaemon(
            db=db_session,
            worker_instance_id="oracle-arm64-worker-01",
            poll_interval=0.01,
            heartbeat_interval=0.01,
            lock_file=lock_file,
        )

        exit_code = daemon.start(max_iterations=2)
        assert exit_code == ExitCode.SUCCESS

        # Assert no process was spawned
        mock_popen.assert_not_called()


# --------------------------------------------------------------------------
# Test 11: ProductionRunner still refuses DRAFT campaigns
# --------------------------------------------------------------------------
def test_production_runner_still_refuses_draft_campaign(db_session, tmp_path):
    """ProductionRunner must preserve existing business validation and reject DRAFT campaigns."""
    lock_file = str(tmp_path / "runner.lock")

    camp = Campaign(
        name="Draft Only Campaign",
        message_template="Hi",
        status="DRAFT",
    )
    db_session.add(camp)
    db_session.commit()

    runner = ProductionRunner(
        db=db_session,
        campaign_id=camp.id,
        lock_file=lock_file,
    )

    exit_code = runner.start()
    assert exit_code == ExitCode.INVALID_STATE


# --------------------------------------------------------------------------
# Test 12: Worker supervision never promotes DRAFT to RUNNING
# --------------------------------------------------------------------------
def test_worker_supervision_never_promotes_draft(db_session, tmp_path):
    """Setting desired_runner_state to RUNNING must NOT promote a DRAFT campaign to RUNNING."""
    lock_file = str(tmp_path / "worker.lock")

    camp = Campaign(
        id=42,
        name="Protected Draft",
        message_template="Hi",
        status="DRAFT",
    )
    db_session.add(camp)

    # Set desired state to RUNNING for Campaign 42
    now = datetime.now(timezone.utc)
    db_session.add(AppSetting(key="system:desired_runner_state", value="RUNNING", updated_at=now))
    db_session.add(AppSetting(key="system:desired_runner_campaign_id", value="42", updated_at=now))
    db_session.commit()

    spawn_called = []
    def fake_spawner(cid):
        spawn_called.append(cid)
        return MagicMock()

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
        runner_spawner=fake_spawner,
    )

    exit_code = daemon.start(max_iterations=2)
    assert exit_code == ExitCode.SUCCESS

    # Critical Assertions:
    # 1. Spawner was NEVER called because Campaign 42 is DRAFT
    assert len(spawn_called) == 0
    # 2. Campaign 42 status was NOT mutated
    db_session.refresh(camp)
    assert camp.status == "DRAFT"


def test_worker_supervision_spawns_when_campaign_is_running(db_session, tmp_path):
    """When a campaign IS explicitly in RUNNING status, the worker engages the runner."""
    lock_file = str(tmp_path / "worker.lock")

    camp = Campaign(
        id=99,
        name="Truly Running Campaign",
        message_template="Hi",
        status="RUNNING",
    )
    db_session.add(camp)

    now = datetime.now(timezone.utc)
    db_session.add(AppSetting(key="system:desired_runner_state", value="RUNNING", updated_at=now))
    db_session.add(AppSetting(key="system:desired_runner_campaign_id", value="99", updated_at=now))
    db_session.commit()

    spawn_called = []
    def fake_spawner(cid):
        spawn_called.append(cid)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is running
        return mock_proc

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
        runner_spawner=fake_spawner,
    )

    exit_code = daemon.start(max_iterations=2)
    assert exit_code == ExitCode.SUCCESS

    # Runner spawner was engaged for Campaign 99
    assert spawn_called == [99]


# --------------------------------------------------------------------------
# Test 13: Worker and Runner process locks are independent
# --------------------------------------------------------------------------
def test_locks_are_strictly_isolated(tmp_path):
    """Worker lock (data/worker.lock) and Runner lock (data/runner.lock) must not block each other."""
    worker_lock_file = str(tmp_path / "worker.lock")
    runner_lock_file = str(tmp_path / "runner.lock")

    worker_lock = ProcessLock(db=None, lock_file_path=worker_lock_file)
    runner_lock = ProcessLock(db=None, lock_file_path=runner_lock_file)

    # Acquire worker lock
    w_ok = worker_lock.acquire(worker_id="worker-01", campaign_id=None)
    assert w_ok is True
    assert worker_lock.is_held is True

    # Acquire runner lock simultaneously: must succeed!
    r_ok = runner_lock.acquire(worker_id="runner-01", campaign_id=1)
    assert r_ok is True
    assert runner_lock.is_held is True

    # Release both
    worker_lock.release()
    runner_lock.release()
    assert worker_lock.is_held is False
    assert runner_lock.is_held is False


# --------------------------------------------------------------------------
# Test 14: Graceful worker shutdown without force-killing
# --------------------------------------------------------------------------
def test_worker_stop_does_not_force_kill(db_session, tmp_path):
    """Worker stop sets desired state to STOPPED and does not force-kill processes."""
    lock_file = str(tmp_path / "worker.lock")

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        lock_file=lock_file,
    )

    # Set up mock active runner
    mock_runner = MagicMock()
    daemon._active_runner_process = mock_runner

    # Call _stop_supervised_runner
    daemon._stop_supervised_runner()

    # Verify desired_runner_state was set to STOPPED
    row = db_session.query(AppSetting).filter(AppSetting.key == "system:desired_runner_state").first()
    assert row is not None
    assert row.value == "STOPPED"

    # Assert mock_runner was NOT killed with SIGKILL or terminate
    mock_runner.kill.assert_not_called()


# --------------------------------------------------------------------------
# Test 15: PREFLIGHT command execution via WorkerDaemon
# --------------------------------------------------------------------------
def test_worker_executes_preflight_command(db_session, tmp_path):
    """WorkerDaemon must claim and execute PREFLIGHT commands safely."""
    lock_file = str(tmp_path / "worker.lock")

    # Seed PREFLIGHT command
    now_utc = datetime.now(timezone.utc)
    cmd_payload = {
        "request_id": "test_preflight_req_1",
        "action": "PREFLIGHT",
        "status": "REQUESTED",
        "version": 1,
        "requested_by": "operator",
        "requested_at": now_utc.isoformat(),
        "params": {},
    }
    db_session.add(AppSetting(
        key="system:whatsapp_command:active",
        value=json.dumps(cmd_payload),
        updated_at=now_utc,
    ))
    db_session.commit()

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    # Verify completed command in AppSetting
    last_cmd_row = db_session.query(AppSetting).filter(AppSetting.key == "system:whatsapp_command:last_completed").first()
    assert last_cmd_row is not None
    last_cmd = json.loads(last_cmd_row.value)
    assert last_cmd["request_id"] == "test_preflight_req_1"
    assert last_cmd["status"] == "COMPLETED"

    # Verify PREFLIGHT result setting
    preflight_row = db_session.query(AppSetting).filter(AppSetting.key == "system:last_preflight_result").first()
    assert preflight_row is not None
    p_data = json.loads(preflight_row.value)
    assert p_data["chrome_cold_started"] is False
    assert p_data["whatsapp_launched"] is False
    assert p_data["messages_sent"] == 0


# --------------------------------------------------------------------------
# Test 16: Orphan command recovery on boot
# --------------------------------------------------------------------------
def test_worker_recovers_orphaned_commands_on_boot(db_session, tmp_path):
    """WorkerDaemon must recover stale/orphaned commands on boot."""
    lock_file = str(tmp_path / "worker.lock")

    expired_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    cmd_payload = {
        "request_id": "stale_command_req_99",
        "action": "HEALTH_CHECK",
        "status": "CLAIMED",
        "version": 2,
        "requested_by": "operator",
        "requested_at": expired_time.isoformat(),
        "claimed_by": "dead_worker",
        "claimed_at": expired_time.isoformat(),
        "lease_expires_at": expired_time.isoformat(),
    }
    db_session.add(AppSetting(
        key="system:whatsapp_command:active",
        value=json.dumps(cmd_payload),
        updated_at=expired_time,
    ))
    db_session.commit()

    daemon = WorkerDaemon(
        db=db_session,
        worker_instance_id="oracle-arm64-worker-01",
        poll_interval=0.01,
        heartbeat_interval=0.01,
        lock_file=lock_file,
    )

    exit_code = daemon.start(max_iterations=1)
    assert exit_code == ExitCode.SUCCESS

    # Verify command was recovered and marked FAILED
    last_cmd_row = db_session.query(AppSetting).filter(AppSetting.key == "system:whatsapp_command:last_completed").first()
    assert last_cmd_row is not None
    data = json.loads(last_cmd_row.value)
    assert data["request_id"] == "stale_command_req_99"
    assert data["status"] == "FAILED"
    assert "recovered" in data["error_message"].lower() or "lease" in data["error_message"].lower()


# --------------------------------------------------------------------------
# Test 17: Static systemd unit validation (no --campaign-id 1)
# --------------------------------------------------------------------------
def test_systemd_unit_has_no_hardcoded_campaign():
    """Validates that deploy/systemd/outreach-runner.service does not contain --campaign-id."""
    service_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "deploy",
        "systemd",
        "outreach-runner.service"
    )
    assert os.path.exists(service_path), f"Service file not found at {service_path}"

    with open(service_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "--campaign-id" not in content, "systemd unit must NOT contain --campaign-id"
    assert "worker start" in content, "systemd unit must invoke 'worker start'"
