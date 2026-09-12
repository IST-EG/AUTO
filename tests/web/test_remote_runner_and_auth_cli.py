"""
Tests for Remote Runner Desired-State Coordination and CLI Auth Bootstrap.

Verifies:
1. Database-mediated desired state coordination (RUNNING/STOPPED) for serverless Control Plane.
2. CLI owner bootstrap command ('outreach auth bootstrap-owner') for secure initial provisioning.
3. Dialect-aware connection engine behavior.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from argparse import Namespace

from app.models.campaign import Campaign
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.web.services.runner_control_service import RunnerControlService
from app.cli.commands.auth import handle_auth_bootstrap_owner
from app.cli.exit_codes import ExitCode
from app.utils.settings import settings


def test_desired_state_get_and_set(web_session):
    """Verifies getting and setting system:desired_runner_state in AppSetting."""
    info = RunnerControlService.get_desired_state(web_session)
    assert info["desired_state"] == "STOPPED"
    assert info["desired_campaign_id"] is None

    RunnerControlService.set_desired_state(web_session, state="RUNNING", campaign_id=42)
    info = RunnerControlService.get_desired_state(web_session)
    assert info["desired_state"] == "RUNNING"
    assert info["desired_campaign_id"] == 42

    RunnerControlService.set_desired_state(web_session, state="STOPPED")
    info = RunnerControlService.get_desired_state(web_session)
    assert info["desired_state"] == "STOPPED"


def test_remote_runner_start_and_stop_coordination(web_session):
    """Verifies that in remote coordination mode, start and stop do not use local OS primitives."""
    camp = Campaign(
        name="Remote Test Campaign",
        message_template="Hello {name}",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    mock_preflight = MagicMock(passed=True, checks=[])

    with patch.object(settings, "RUNNER_REMOTE_COORDINATION", True), \
         patch("app.web.services.runner_control_service.run_preflight", return_value=mock_preflight), \
         patch("app.web.services.runner_control_service.subprocess.Popen") as mock_popen, \
         patch("os.kill") as mock_kill:

        # 1. Start Runner in remote mode
        success, msg, data = RunnerControlService.start_runner(
            db=web_session,
            campaign_id=camp.id,
            operator_username="operator_remote"
        )
        assert success is True
        assert data["desired_state"] == "RUNNING"
        assert data["pid"] is None
        mock_popen.assert_not_called()

        # Check DB setting was recorded
        state_info = RunnerControlService.get_desired_state(web_session)
        assert state_info["desired_state"] == "RUNNING"
        assert state_info["desired_campaign_id"] == camp.id

        # 2. Stop Runner in remote mode
        stop_success, stop_msg, stop_data = RunnerControlService.stop_runner(
            db=web_session,
            operator_username="operator_remote"
        )
        assert stop_success is True
        assert stop_data["desired_state"] == "STOPPED"
        mock_kill.assert_not_called()

        state_info = RunnerControlService.get_desired_state(web_session)
        assert state_info["desired_state"] == "STOPPED"


def test_cli_auth_bootstrap_owner(web_session):
    """Verifies that CLI 'outreach auth bootstrap-owner' securely creates the initial OWNER."""
    # Ensure zero users exist
    web_session.query(User).delete()
    web_session.query(AppSetting).filter(AppSetting.key == "system_bootstrap_owner_lock").delete()
    web_session.commit()

    args = Namespace(
        username="admin_cli",
        email="admin_cli@integra-ist.com",
        password="SecureAdmin123!@#"
    )

    exit_code = handle_auth_bootstrap_owner(args, web_session)
    assert exit_code == ExitCode.SUCCESS

    # Verify user created with role OWNER
    user = web_session.query(User).filter(User.username == "admin_cli").first()
    assert user is not None
    assert user.role == UserRole.OWNER.value
    assert user.email == "admin_cli@integra-ist.com"

    # Verify audit log emitted
    audit = web_session.query(AuditLog).filter(AuditLog.event_type == "SYSTEM_BOOTSTRAP_OWNER_CREATED").first()
    assert audit is not None
    assert audit.status == "SUCCESS"

    # Verify subsequent bootstrap attempt is rejected
    args_second = Namespace(
        username="attacker",
        email="attacker@example.com",
        password="AnotherPassword123!@#"
    )
    exit_code_second = handle_auth_bootstrap_owner(args_second, web_session)
    assert exit_code_second == ExitCode.GENERAL_ERROR


def test_cli_auth_bootstrap_weak_password(web_session):
    """Rejects weak password through CLI bootstrap."""
    web_session.query(User).delete()
    web_session.query(AppSetting).filter(AppSetting.key == "system_bootstrap_owner_lock").delete()
    web_session.commit()

    args = Namespace(
        username="admin_cli2",
        email="admin_cli2@integra-ist.com",
        password="weak"
    )

    exit_code = handle_auth_bootstrap_owner(args, web_session)
    assert exit_code == ExitCode.GENERAL_ERROR

    user = web_session.query(User).filter(User.username == "admin_cli2").first()
    assert user is None


def test_cli_auth_bootstrap_password_prompt(web_session, monkeypatch):
    """Verifies that --password-prompt securely prompts via getpass and creates OWNER."""
    import getpass
    web_session.query(User).delete()
    web_session.query(AppSetting).filter(AppSetting.key == "system_bootstrap_owner_lock").delete()
    web_session.commit()

    monkeypatch.setattr(getpass, "getpass", lambda prompt: "ValidPassword123!@#")

    args = Namespace(
        username="prompt_admin",
        email="prompt_admin@integra-ist.com",
        password=None,
        password_prompt=True
    )

    exit_code = handle_auth_bootstrap_owner(args, web_session)
    assert exit_code == ExitCode.SUCCESS

    user = web_session.query(User).filter(User.username == "prompt_admin").first()
    assert user is not None
    assert user.role == UserRole.OWNER.value

