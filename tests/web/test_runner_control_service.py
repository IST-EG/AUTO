"""
Tests for RunnerControlService.

Verifies:
- Runner status detection (STOPPED, RUNNING, STALE_LOCK_DETECTED)
- Singularity enforcement via OS ProcessLock (authoritative guard)
- Campaign state validation (rejects non-RUNNING campaigns)
- Audit log emission for start and stop operations
- Graceful stopping and stale state recovery
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app.models.campaign import Campaign
from app.models.audit_log import AuditLog
from app.models.user import UserRole
from app.runner.process_lock import ProcessLock
from app.web.services.runner_control_service import RunnerControlService


def test_get_status_stopped(web_session):
    """When no runner is active, returns STOPPED state."""
    status = RunnerControlService.get_status(web_session)
    assert status["is_running"] is False
    assert status["state"] == "STOPPED"
    assert status["pid"] is None
    assert status["campaign_id"] is None


def test_get_status_running_fresh(web_session):
    """When active runner PID is alive with fresh heartbeat, returns RUNNING."""
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_runner_info = {
        "pid": 54321,
        "worker_id": "worker-prod-1",
        "campaign_id": 42,
        "started_at": now_iso,
        "last_heartbeat": now_iso,
    }

    with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_runner_info), \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=True):
        status = RunnerControlService.get_status(web_session)
        assert status["is_running"] is True
        assert status["state"] == "RUNNING"
        assert status["pid"] == 54321
        assert status["campaign_id"] == 42


def test_get_status_stale_lock_dead_pid(web_session):
    """When lock info exists but PID is dead, returns STALE_LOCK_DETECTED."""
    mock_runner_info = {
        "pid": 54321,
        "worker_id": "worker-prod-1",
        "campaign_id": 42,
    }

    with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_runner_info), \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=False):
        status = RunnerControlService.get_status(web_session)
        assert status["is_running"] is False
        assert status["state"] == "STALE_LOCK_DETECTED"
        assert status["is_stale"] is True


def test_start_runner_nonexistent_campaign(web_session):
    """Fails when campaign ID does not exist."""
    success, message, data = RunnerControlService.start_runner(
        db=web_session,
        campaign_id=99999,
        operator_username="test_op"
    )
    assert success is False
    assert "was not found" in message
    assert data is None


def test_start_runner_campaign_not_running(web_session):
    """Fails when campaign is in DRAFT status."""
    camp = Campaign(
        name="Draft Campaign",
        message_template="Hello",
        status="DRAFT",
    )
    web_session.add(camp)
    web_session.commit()

    success, message, data = RunnerControlService.start_runner(
        db=web_session,
        campaign_id=camp.id,
        operator_username="test_op"
    )
    assert success is False
    assert "is not in RUNNING state" in message
    assert data is None


def test_start_runner_lock_conflict(web_session):
    """Fails when another runner PID is actively running (OS lock singularity)."""
    camp = Campaign(
        name="Running Campaign",
        message_template="Hello",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    active_info = {"pid": 11111, "campaign_id": 1}

    with patch.object(ProcessLock, "get_active_runner_info", return_value=active_info), \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=True):

        success, message, data = RunnerControlService.start_runner(
            db=web_session,
            campaign_id=camp.id,
            operator_username="test_op"
        )
        assert success is False
        assert "another production runner process is actively running" in message
        assert data is None


def test_start_runner_preflight_failure(web_session):
    """Fails when preflight checks fail."""
    camp = Campaign(
        name="Preflight Fail Camp",
        message_template="Hello",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    mock_preflight = MagicMock()
    mock_preflight.passed = False
    mock_preflight.checks = [{"name": "DATABASE", "passed": False, "error": "No DB connection"}]

    with patch.object(ProcessLock, "get_active_runner_info", return_value=None), \
         patch("app.web.services.runner_control_service.run_preflight", return_value=mock_preflight):

        success, message, data = RunnerControlService.start_runner(
            db=web_session,
            campaign_id=camp.id,
            operator_username="test_op"
        )
        assert success is False
        assert "Preflight readiness check failed" in message


def test_start_runner_success(web_session):
    """Starts runner with custom spawner and emits RUNNER_START_REQUESTED audit log."""
    camp = Campaign(
        name="Valid Running Camp",
        message_template="Hello",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    mock_preflight = MagicMock(passed=True, checks=[])
    mock_spawner = MagicMock(return_value=7890)

    with patch.object(ProcessLock, "get_active_runner_info", return_value=None), \
         patch("app.web.services.runner_control_service.run_preflight", return_value=mock_preflight):

        success, message, data = RunnerControlService.start_runner(
            db=web_session,
            campaign_id=camp.id,
            operator_username="operator_bob",
            runner_spawner=mock_spawner
        )

        assert success is True
        assert data["pid"] == 7890
        assert data["campaign_id"] == camp.id
        mock_spawner.assert_called_once_with(camp.id)

        # Verify audit log was recorded
        audit = (
            web_session.query(AuditLog)
            .filter(AuditLog.event_type == "RUNNER_START_REQUESTED")
            .first()
        )
        assert audit is not None
        assert audit.status == "SUCCESS"
        assert audit.campaign_id == camp.id


def test_stop_runner_no_active_runner(web_session):
    """Fails when no runner is active."""
    with patch.object(ProcessLock, "get_active_runner_info", return_value=None):
        success, message, data = RunnerControlService.stop_runner(
            db=web_session,
            operator_username="test_op"
        )
        assert success is False
        assert "No active" in message


def test_stop_runner_success(web_session):
    """Stops active runner process, cleans up lock, and emits RUNNER_STOP_REQUESTED audit log."""
    active_info = {"pid": 65432, "campaign_id": 10}
    mock_stopper = MagicMock()

    with patch.object(ProcessLock, "get_active_runner_info", return_value=active_info), \
         patch.object(ProcessLock, "release") as mock_release, \
         patch("app.web.services.runner_control_service.is_pid_alive", side_effect=[True, False]):

        success, message, data = RunnerControlService.stop_runner(
            db=web_session,
            operator_username="operator_bob",
            timeout_seconds=5,
            stopper_fn=mock_stopper
        )

        assert success is True
        assert data["pid"] == 65432
        mock_stopper.assert_called_once_with(65432)
        mock_release.assert_called_once()
        audit = (
            web_session.query(AuditLog)
            .filter(AuditLog.event_type == "RUNNER_STOP_REQUESTED")
            .first()
        )
        assert audit is not None
        assert audit.status == "SUCCESS"


def test_get_status_degraded_and_unhealthy_heartbeat(web_session):
    """Tests DEGRADED and UNHEALTHY status thresholds based on heartbeat age."""
    now_utc = datetime.now(timezone.utc)
    mock_runner_info = {
        "pid": 54321,
        "worker_id": "worker-prod-1",
        "campaign_id": 42,
        "started_at": now_utc.isoformat(),
        "last_heartbeat": (now_utc - timedelta(seconds=45)).isoformat(),
    }

    with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_runner_info), \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=True):
        status = RunnerControlService.get_status(web_session)
        assert status["state"] == "DEGRADED"

    # UNHEALTHY (>60s)
    mock_runner_info["last_heartbeat"] = (now_utc - timedelta(seconds=75)).isoformat()
    with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_runner_info), \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=True):
        status = RunnerControlService.get_status(web_session)
        assert status["state"] == "UNHEALTHY"


def test_stop_runner_dead_pid_releases_stale_lock(web_session):
    """When stop_runner detects a dead PID, it releases the stale lock immediately."""
    active_info = {"pid": 99999, "campaign_id": 10}

    with patch.object(ProcessLock, "get_active_runner_info", return_value=active_info), \
         patch.object(ProcessLock, "release") as mock_release, \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=False):

        success, message, data = RunnerControlService.stop_runner(
            db=web_session,
            operator_username="operator_bob"
        )
        assert success is True
        assert "already terminated" in message
        mock_release.assert_called_once()


def test_start_runner_stale_lock_recovery(web_session):
    """When start_runner detects a dead PID in lock, it clears the stale lock and proceeds."""
    camp = Campaign(
        name="Recover Camp",
        message_template="Hello",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    active_info = {"pid": 11111, "campaign_id": 1}
    mock_preflight = MagicMock(passed=True, checks=[])
    mock_spawner = MagicMock(return_value=22222)

    with patch.object(ProcessLock, "get_active_runner_info", return_value=active_info), \
         patch.object(ProcessLock, "release") as mock_release, \
         patch("app.web.services.runner_control_service.is_pid_alive", return_value=False), \
         patch("app.web.services.runner_control_service.run_preflight", return_value=mock_preflight):

        success, message, data = RunnerControlService.start_runner(
            db=web_session,
            campaign_id=camp.id,
            operator_username="operator_bob",
            runner_spawner=mock_spawner
        )
        assert success is True
        assert data["pid"] == 22222
        mock_release.assert_called_once()
