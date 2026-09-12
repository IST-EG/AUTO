"""
Integration tests for CLI main routing and command invocation.
"""

import pytest
from unittest.mock import patch, MagicMock
from app.cli.main import main
from app.cli.exit_codes import ExitCode
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message


def test_main_routing_session_status(db_session):
    ret = main(["session", "status"], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)


def test_main_routing_session_logout(db_session):
    ret = main(["session", "logout"], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)


def test_main_routing_session_login_mocked(db_session):
    with patch("app.cli.main.handle_session_login") as mock_login:
        mock_login.return_value = ExitCode.SUCCESS
        ret = main(["session", "login", "--headless"], db_session=db_session)
        assert ret == int(ExitCode.SUCCESS)
        assert mock_login.called


def test_main_routing_campaign_commands(db_session):
    camp = Campaign(
        name="Main Routing Campaign",
        message_template="Hello {{name}}",
        status="DRAFT"
    )
    db_session.add(camp)
    db_session.commit()

    # campaign run
    ret = main(["campaign", "run", str(camp.id)], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)

    # campaign status
    ret = main(["campaign", "status", str(camp.id)], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)

    # campaign pause
    ret = main(["campaign", "pause", str(camp.id)], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)

    # campaign resume
    ret = main(["campaign", "resume", str(camp.id)], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)

    # campaign stop
    ret = main(["campaign", "stop", str(camp.id)], db_session=db_session)
    assert ret == int(ExitCode.SUCCESS)


def test_main_routing_queue_commands(db_session):
    camp = Campaign(name="QueueRouteCamp", message_template="Hi", status="RUNNING")
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Sam", phone_e164="+15556667777", country_code="US")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hi Sam",
        status="QUEUED",
        idempotency_key="main_msg_1"
    )
    db_session.add(msg)
    db_session.commit()

    # queue status
    assert main(["queue", "status", "--campaign-id", str(camp.id)], db_session=db_session) == int(ExitCode.SUCCESS)

    # queue inspect
    assert main(["queue", "inspect", str(msg.id)], db_session=db_session) == int(ExitCode.SUCCESS)

    # queue reconcile
    assert main(["queue", "reconcile", "--campaign-id", str(camp.id)], db_session=db_session) == int(ExitCode.SUCCESS)


def test_main_routing_emergency_commands(db_session):
    assert main(["emergency-status"], db_session=db_session) == int(ExitCode.SUCCESS)
    assert main(["emergency-stop", "--reason", "Main route test"], db_session=db_session) == int(ExitCode.SUCCESS)
    assert main(["emergency-resume", "--reason", "Main route clear"], db_session=db_session) == int(ExitCode.SUCCESS)


def test_main_routing_runner_commands(db_session):
    # runner status
    assert main(["runner", "status"], db_session=db_session) == int(ExitCode.SUCCESS)

    # runner stop when inactive
    assert main(["runner", "stop"], db_session=db_session) == int(ExitCode.SUCCESS)

    # runner start without campaign_id is rejected by parser, with campaign_id runs runner
    with patch("app.cli.main.handle_runner_start") as mock_start:
        mock_start.return_value = ExitCode.SUCCESS
        ret = main(["runner", "start", "--campaign-id", "1"], db_session=db_session)
        assert ret == int(ExitCode.SUCCESS)
        assert mock_start.called


def test_main_routing_queue_override(db_session):
    with patch("app.cli.main.handle_queue_override") as mock_override:
        mock_override.return_value = ExitCode.SUCCESS
        ret = main(["queue", "override", "1", "--reason", "Verified"], db_session=db_session)
        assert ret == int(ExitCode.SUCCESS)
        assert mock_override.called


def test_main_missing_subcommands(db_session):
    assert main(["session"], db_session=db_session) == int(ExitCode.INVALID_ARGUMENT)
    assert main(["campaign"], db_session=db_session) == int(ExitCode.INVALID_ARGUMENT)
    assert main(["runner"], db_session=db_session) == int(ExitCode.INVALID_ARGUMENT)
    assert main(["queue"], db_session=db_session) == int(ExitCode.INVALID_ARGUMENT)


def test_main_unexpected_exception(db_session):
    with patch("app.cli.main.handle_session_status", side_effect=RuntimeError("Surprise!")):
        ret = main(["session", "status"], db_session=db_session)
        assert ret == int(ExitCode.GENERAL_ERROR)


def test_main_default_session_lifecycle():
    with patch("app.cli.main.SessionLocal") as mock_session_local:
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        ret = main(["session", "status"])
        assert ret == int(ExitCode.SUCCESS)
        assert mock_db.close.called

