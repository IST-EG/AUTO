"""
Unit tests for the 10-point production preflight and readiness inspection matrix.
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from collections import namedtuple
from app.cli.exit_codes import ExitCode
from app.readiness.preflight import (
    CheckResult,
    PreflightResult,
    check_python_runtime,
    check_configuration,
    check_directories_and_permissions,
    check_database_connectivity,
    check_database_schema,
    check_browser_environment,
    check_session_authentication,
    check_process_lock_singularity,
    check_emergency_stop_status,
    check_circuit_breaker,
    run_preflight,
    settings,
)
from app.models.campaign import Campaign
from app.models.app_setting import AppSetting
from app.runner.process_lock import ProcessLock


def test_check_python_runtime():
    # Real runtime should pass on Python >= 3.8
    result = check_python_runtime()
    assert result.passed is True
    assert result.exit_code == ExitCode.SUCCESS
    assert "Python" in result.message

    # Mock older Python
    VersionInfo = namedtuple("VersionInfo", ["major", "minor", "micro"])
    with patch.object(sys, "version_info", VersionInfo(3, 7, 0)):
        res_fail = check_python_runtime()
        assert res_fail.passed is False
        assert res_fail.exit_code == ExitCode.GENERAL_ERROR


def test_check_configuration():
    result = check_configuration()
    assert result.passed is True
    assert result.exit_code == ExitCode.SUCCESS

    with patch.object(settings, "GLOBAL_DAILY_LIMIT", 0):
        res_fail = check_configuration()
        assert res_fail.passed is False
        assert res_fail.exit_code == ExitCode.INVALID_ARGUMENT


def test_check_directories_and_permissions(tmp_path):
    result = check_directories_and_permissions()
    assert result.passed is True
    assert result.exit_code == ExitCode.SUCCESS

    # Simulate unwritable dir by raising exception in mkdir
    with patch("pathlib.Path.mkdir", side_effect=PermissionError("Mock Permission Denied")):
        res_fail = check_directories_and_permissions()
        assert res_fail.passed is False
        assert res_fail.exit_code == ExitCode.GENERAL_ERROR


def test_check_database_connectivity_and_schema(db_session):
    # Connectivity
    res_conn = check_database_connectivity(db_session)
    assert res_conn.passed is True
    assert res_conn.exit_code == ExitCode.SUCCESS

    # Schema
    res_schema = check_database_schema(db_session)
    assert res_schema.passed is True
    assert res_schema.exit_code == ExitCode.SUCCESS

    # Connectivity failure
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("DB Connection Refused")
    res_fail = check_database_connectivity(mock_db)
    assert res_fail.passed is False
    assert res_fail.exit_code == ExitCode.GENERAL_ERROR


def test_check_browser_environment():
    # If Chrome is found
    with patch("shutil.which", return_value="/mock/bin/google-chrome"):
        res = check_browser_environment()
        assert res.passed is True
        assert res.exit_code == ExitCode.SUCCESS

    # If Chrome is not found
    with patch("shutil.which", return_value=None), \
         patch("os.path.exists", return_value=False):
        res_fail = check_browser_environment()
        assert res_fail.passed is False
        assert res_fail.exit_code == ExitCode.PROVIDER_UNAVAILABLE


def test_check_session_authentication(tmp_path):
    fake_session_dir = tmp_path / "mock_session"
    fake_session_dir.mkdir()
    (fake_session_dir / "Default").mkdir()

    with patch.object(settings, "WHATSAPP_SESSION_PATH", str(fake_session_dir)):
        res = check_session_authentication()
        assert res.passed is True
        assert res.exit_code == ExitCode.SUCCESS

    # Non-existent session dir (warning, non-critical)
    with patch.object(settings, "WHATSAPP_SESSION_PATH", str(tmp_path / "non_existent")):
        res_warn = check_session_authentication()
        assert res_warn.passed is False
        assert res_warn.critical is False
        assert res_warn.exit_code == ExitCode.AUTHENTICATION_REQUIRED


def test_check_process_lock_singularity(db_session):
    # When no other process holds lock
    res = check_process_lock_singularity(db_session)
    assert res.passed is True

    # When another live process holds lock
    mock_info = {"pid": 999999, "worker_id": "other-worker"}
    with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_info), \
         patch("app.readiness.preflight.is_pid_alive", return_value=True):
        res_fail = check_process_lock_singularity(db_session)
        assert res_fail.passed is False
        assert res_fail.exit_code == ExitCode.CONCURRENCY_ERROR

    # When own process holds lock (e.g. during runner startup)
    mock_info_self = {"pid": os.getpid(), "worker_id": "self-worker"}
    with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_info_self):
        res_self = check_process_lock_singularity(db_session)
        assert res_self.passed is True


def test_check_emergency_stop_status(db_session):
    # Inactive
    res = check_emergency_stop_status(db_session)
    assert res.passed is True

    # Active
    setting = db_session.query(AppSetting).filter(AppSetting.key == "emergency_stop").first()
    if not setting:
        setting = AppSetting(key="emergency_stop", value=json.dumps({"active": True, "reason": "Test Killswitch"}))
        db_session.add(setting)
    else:
        setting.value = json.dumps({"active": True, "reason": "Test Killswitch"})
    db_session.commit()

    res_active = check_emergency_stop_status(db_session)
    assert res_active.passed is False
    assert res_active.exit_code == ExitCode.EMERGENCY_STOP_ACTIVE
    assert "Killswitch" in res_active.message

    # Reset
    setting.value = json.dumps({"active": False})
    db_session.commit()


def test_check_circuit_breaker(db_session):
    # No campaign target specified
    res_none = check_circuit_breaker(db_session, None)
    assert res_none.passed is True

    # Target campaign exists and running
    camp = Campaign(name="Preflight Camp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    res_ok = check_circuit_breaker(db_session, camp.id)
    assert res_ok.passed is True

    # Target campaign paused
    camp.status = "PAUSED"
    db_session.commit()

    res_paused = check_circuit_breaker(db_session, camp.id)
    assert res_paused.passed is False
    assert res_paused.exit_code == ExitCode.CIRCUIT_BREAKER_OPEN


def test_run_preflight_modes(db_session):
    # Injected provider bypasses browser checks cleanly
    mock_provider = MagicMock()
    mock_provider.__class__.__name__ = "MockMessageProvider"

    res_std = run_preflight(db_session, strict=False, provider=mock_provider)
    assert res_std.passed is True
    assert res_std.exit_code == ExitCode.SUCCESS
    assert len(res_std.checks) == 10
    assert all(c.passed for c in res_std.checks)

    # Strict mode test with unauthenticated session elevating warning to critical
    with patch("app.readiness.preflight.check_session_authentication") as mock_sess:
        mock_sess.return_value = CheckResult("Session Authentication", False, "Unauthenticated", ExitCode.AUTHENTICATION_REQUIRED, critical=False)
        res_strict = run_preflight(db_session, strict=True, provider=None)
        # In strict mode, critical=True on failure so res_strict.passed should be False
        assert res_strict.passed is False
        assert res_strict.exit_code == ExitCode.AUTHENTICATION_REQUIRED
