"""
Integration tests for CLI commands:
- outreach analytics <campaign|queue|runner|provider|system>
- outreach preflight
- outreach system health
"""

import json
import pytest
from app.cli.main import main
from app.cli.exit_codes import ExitCode
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.queue.state_machine import QueueState


def test_cli_analytics_campaign_json_and_table(db_session, capsys):
    camp = Campaign(
        name="Analytics CLI Campaign",
        message_template="Hello {{name}}",
        status="RUNNING",
        daily_limit=50,
    )
    db_session.add(camp)
    db_session.flush()

    contact = Contact(name="Alice", phone_e164="+201012345678", country_code="EG")
    db_session.add(contact)
    db_session.flush()

    msg = Message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Alice",
        status=QueueState.SENT,
        idempotency_key="cli_test_msg_1",
    )
    db_session.add(msg)
    db_session.commit()

    # 1. Standard tabular output
    code = main(["analytics", "campaign", str(camp.id)], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "CAMPAIGN ANALYTICS:" in captured.out
    assert "Confirmed Send Rate" in captured.out
    assert "100.00%" in captured.out

    # 2. JSON output
    code_json = main(["analytics", "campaign", str(camp.id), "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["campaign_id"] == camp.id
    assert data["performance"]["confirmed_send_rate"] == 100.0


def test_cli_analytics_campaign_not_found(db_session, capsys):
    code = main(["analytics", "campaign", "99999"], db_session=db_session)
    assert code == int(ExitCode.NOT_FOUND)
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_cli_analytics_queue(db_session, capsys):
    code = main(["analytics", "queue"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "QUEUE ANALYTICS" in captured.out

    code_json = main(["analytics", "queue", "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert "backlog" in data
    assert "confirmed_throughput" in data


def test_cli_analytics_runner(db_session, capsys):
    code = main(["analytics", "runner"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "PRODUCTION RUNNER ANALYTICS" in captured.out

    code_json = main(["analytics", "runner", "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert "is_running" in data
    assert "uptime_seconds" in data


def test_cli_analytics_provider(db_session, capsys):
    code = main(["analytics", "provider"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "PROVIDER ANALYTICS" in captured.out

    code_json = main(["analytics", "provider", "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["provider_name"] == "WhatsAppWebProvider"


def test_cli_analytics_system(db_session, capsys):
    code = main(["analytics", "system"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "OUTREACH SYSTEM EXECUTIVE DASHBOARD" in captured.out

    code_json = main(["analytics", "system", "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert "daily_quota" in data
    assert "campaigns" in data


def test_cli_preflight(db_session, capsys):
    code = main(["preflight"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "PRODUCTION PREFLIGHT & READINESS INSPECTION" in captured.out
    assert "Preflight inspection PASSED" in captured.out

    code_json = main(["preflight", "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["passed"] is True
    assert len(data["checks"]) == 11



def test_cli_system_health(db_session, capsys):
    code = main(["system", "health"], db_session=db_session)
    assert code == int(ExitCode.SUCCESS)
    captured = capsys.readouterr()
    assert "OPERATIONAL SYSTEM HEALTH" in captured.out

    code_json = main(["system", "health", "--json"], db_session=db_session)
    assert code_json == int(ExitCode.SUCCESS)
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert "state" in data
    assert "details" in data


def test_cli_system_health_degraded_and_unhealthy(db_session, capsys):
    from unittest.mock import patch
    from app.readiness.health import HealthResult, HealthState

    # Degraded
    mock_deg = HealthResult(
        state=HealthState.DEGRADED,
        reasons=["Heartbeat lagging"],
        details={"database_responsive": True, "emergency_stop_active": False, "runner": {}, "queue": {"depth": 0, "stale_leases": 0, "unknown_outcomes": 1}},
    )
    with patch("app.cli.commands.health.evaluate_system_health", return_value=mock_deg):
        main(["system", "health"], db_session=db_session)
        captured = capsys.readouterr()
        assert "DEGRADED" in captured.out

    # Unhealthy
    mock_unh = HealthResult(
        state=HealthState.UNHEALTHY,
        reasons=["Database down"],
        details={"database_responsive": False, "emergency_stop_active": False, "runner": {}, "queue": {"depth": 0, "stale_leases": 0, "unknown_outcomes": 0}},
    )
    with patch("app.cli.commands.health.evaluate_system_health", return_value=mock_unh):
        main(["system", "health"], db_session=db_session)
        captured = capsys.readouterr()
        assert "UNHEALTHY" in captured.out

