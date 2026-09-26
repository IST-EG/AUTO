"""
Unit and Integration Tests for Worker CLI Commands.

Tests:
1. Argument parser for 'outreach worker <start|status|stop>'.
2. 'outreach worker start' fails when WORKER_INSTANCE_ID is unconfigured/invalid.
3. 'outreach worker start' succeeds with valid WORKER_INSTANCE_ID.
4. 'outreach worker status' when daemon is stopped.
5. 'outreach worker status' when daemon is active.
6. 'outreach worker stop' when no worker is running.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

from app.cli.main import build_parser, main
from app.cli.exit_codes import ExitCode
from app.runner.process_lock import ProcessLock
from app.models.app_setting import AppSetting
from app.utils.settings import settings


def test_worker_cli_parser():
    """Validates CLI argument parsing for worker subcommands."""
    parser = build_parser()

    # 1. worker start
    args_start = parser.parse_args(["worker", "start", "--poll-interval", "3", "--heartbeat-interval", "10", "--max-iterations", "5"])
    assert args_start.command == "worker"
    assert args_start.subcommand == "start"
    assert args_start.poll_interval == 3
    assert args_start.heartbeat_interval == 10
    assert args_start.max_iterations == 5

    # 2. worker status
    args_status = parser.parse_args(["worker", "status"])
    assert args_status.command == "worker"
    assert args_status.subcommand == "status"

    # 3. worker stop
    args_stop = parser.parse_args(["worker", "stop"])
    assert args_stop.command == "worker"
    assert args_stop.subcommand == "stop"


def test_worker_cli_start_unconfigured(db_session, monkeypatch):
    """'outreach worker start' fails with INVALID_ARGUMENT if WORKER_INSTANCE_ID is unconfigured."""
    monkeypatch.setattr(settings, "WORKER_INSTANCE_ID", "")

    code = main(["worker", "start"], db_session=db_session)
    assert code == int(ExitCode.INVALID_ARGUMENT)


def test_worker_cli_start_success(db_session, tmp_path, monkeypatch):
    """'outreach worker start' succeeds when WORKER_INSTANCE_ID is configured."""
    lock_file = str(tmp_path / "worker_cli.lock")
    monkeypatch.setattr(settings, "WORKER_INSTANCE_ID", "oracle-arm64-worker-01")
    monkeypatch.setattr(settings, "WORKER_LOCK_FILE", lock_file)

    code = main(["worker", "start", "--max-iterations", "1"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)


def test_worker_cli_status_stopped(db_session, tmp_path, monkeypatch, capsys):
    """'outreach worker status' reports STOPPED when no daemon is active."""
    lock_file = str(tmp_path / "worker_cli_empty.lock")
    monkeypatch.setattr(settings, "WORKER_LOCK_FILE", lock_file)

    code = main(["worker", "status"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)

    captured = capsys.readouterr()
    assert "WORKER DAEMON STATUS" in captured.out.upper()
    assert "STOPPED" in captured.out


def test_worker_cli_status_active(db_session, tmp_path, monkeypatch, capsys):
    """'outreach worker status' reports ACTIVE when worker lock is held."""
    lock_file = str(tmp_path / "worker_cli_active.lock")
    monkeypatch.setattr(settings, "WORKER_LOCK_FILE", lock_file)

    # Acquire lock with current PID
    lock = ProcessLock(db=None, lock_file_path=lock_file)
    acquired = lock.acquire(worker_id="oracle-arm64-worker-01", campaign_id=None)
    assert acquired is True

    try:
        code = main(["worker", "status"], db_session=db_session)
        assert code == int(ExitCode.SUCCESS)

        captured = capsys.readouterr()
        assert "ACTIVE" in captured.out
        assert "oracle-arm64-worker-01" in captured.out
    finally:
        lock.release()


def test_worker_cli_stop_no_process(db_session, tmp_path, monkeypatch, capsys):
    """'outreach worker stop' handles clean state when no daemon is active."""
    lock_file = str(tmp_path / "worker_cli_stop.lock")
    monkeypatch.setattr(settings, "WORKER_LOCK_FILE", lock_file)

    code = main(["worker", "stop"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)

    captured = capsys.readouterr()
    assert "No active worker daemon process was detected" in captured.out
