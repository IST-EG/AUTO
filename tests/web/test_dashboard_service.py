"""
Tests for DashboardService.

Verifies:
- Complete dashboard snapshot aggregation conforming to DashboardSnapshotDTO schema
- Confirmed send rate calculation with locked formula:
    Confirmed / (Confirmed + Failed + Unknown Outcome) * 100
- Queue analytics integration and UNKNOWN_OUTCOME tracking
- Dynamic operational alert generation
- Graceful error resilience on database failure
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.contact import Contact
from app.models.message import Message
from app.models.audit_log import AuditLog
from app.web.services.dashboard_service import DashboardService
from app.web.schemas.dashboard import DashboardSnapshotDTO
from app.scheduler.emergency_stop import EmergencyStop


def test_dashboard_snapshot_empty_db(web_session):
    """Snapshot with empty database succeeds and produces valid DashboardSnapshotDTO."""
    snapshot = DashboardService.get_dashboard_snapshot(web_session)
    assert isinstance(snapshot, dict)

    dto = DashboardSnapshotDTO(**snapshot)
    assert dto.system_health.state in ["HEALTHY", "DEGRADED", "UNHEALTHY", "STOPPED"]
    assert dto.database.connected is True
    assert dto.database.status == "CONNECTED"
    assert dto.active_campaign is None
    assert dto.queue.queued == 0
    assert dto.queue.confirmed_sends == 0
    assert dto.runner.state == "STOPPED"


def test_dashboard_snapshot_with_active_campaign_and_rate(web_session):
    """Snapshot with active campaign computes locked Confirmed Send Rate."""
    now_utc = datetime.now(timezone.utc)

    # 1. Create campaign in RUNNING status
    camp = Campaign(
        name="Q3 Outreach Test",
        message_template="Hello {name}",
        status="RUNNING",
        min_delay_seconds=5,
        max_delay_seconds=15,
        daily_limit=50,
        error_threshold=5,
    )
    web_session.add(camp)
    web_session.commit()

    # 2. Create contacts and messages:
    # 4 SENT, 1 FAILED (error_type=PERMANENT), 1 FAILED (error_type=UNKNOWN_OUTCOME)
    # Total completed denominator = 4 + 1 + 1 = 6
    # Confirmed Send Rate = 4 / 6 * 100 = 66.67%
    contacts = []
    for i in range(6):
        c = Contact(
            phone_e164=f"+20101234567{i}",
            country_code="EG",
            name=f"Contact {i}",
        )
        web_session.add(c)
        contacts.append(c)
    web_session.commit()

    for i, c in enumerate(contacts):
        cc = CampaignContact(campaign_id=camp.id, contact_id=c.id, status="ELIGIBLE")
        web_session.add(cc)
        web_session.flush()

        if i < 4:
            m = Message(
                campaign_id=camp.id,
                campaign_contact_id=cc.id,
                contact_id=c.id,
                idempotency_key=f"msg_test_{i}",
                rendered_content="Hello",
                status="SENT",
                sent_at=now_utc,
            )
        elif i == 4:
            m = Message(
                campaign_id=camp.id,
                campaign_contact_id=cc.id,
                contact_id=c.id,
                idempotency_key=f"msg_test_{i}",
                rendered_content="Hello",
                status="FAILED",
                error_type="PERMANENT",
                failed_at=now_utc,
            )
        else:
            m = Message(
                campaign_id=camp.id,
                campaign_contact_id=cc.id,
                contact_id=c.id,
                idempotency_key=f"msg_test_{i}",
                rendered_content="Hello",
                status="FAILED",
                error_type="UNKNOWN_OUTCOME",
                failed_at=now_utc,
            )
        web_session.add(m)
    web_session.commit()

    snapshot = DashboardService.get_dashboard_snapshot(web_session)
    dto = DashboardSnapshotDTO(**snapshot)

    assert dto.active_campaign is not None
    assert dto.active_campaign.id == camp.id
    assert dto.active_campaign.name == "Q3 Outreach Test"
    assert dto.active_campaign.send_confirmed == 4
    assert dto.active_campaign.failed == 1
    assert dto.active_campaign.unknown_outcome == 1
    assert dto.active_campaign.confirmed_send_rate == 66.67


def test_dashboard_snapshot_alerts_generation(web_session):
    """Verifies operational alerts generated for unknown_outcome, emergency stop, and missing WA profile."""
    # Create an unknown outcome message to trigger ALERT_UNKNOWN_OUTCOME
    camp = Campaign(
        name="Alerts Camp",
        message_template="Hello",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    c = Contact(phone_e164="+201099999999", country_code="EG", name="Alert Contact")
    web_session.add(c)
    web_session.commit()

    cc = CampaignContact(campaign_id=camp.id, contact_id=c.id, status="ELIGIBLE")
    web_session.add(cc)
    web_session.flush()

    m = Message(
        campaign_id=camp.id,
        campaign_contact_id=cc.id,
        contact_id=c.id,
        idempotency_key="alert_msg_1",
        rendered_content="Test",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
    )
    web_session.add(m)
    web_session.commit()

    # Trigger emergency stop
    e_stop = EmergencyStop(web_session)
    e_stop.trigger(reason="Test critical alert")

    # Mock non-existent WA profile
    with patch("os.path.exists", return_value=False):
        snapshot = DashboardService.get_dashboard_snapshot(web_session)
        dto = DashboardSnapshotDTO(**snapshot)

        alert_ids = [a.id for a in dto.alerts]
        assert "ALERT_UNKNOWN_OUTCOME" in alert_ids
        assert "ALERT_EMERGENCY_STOP" in alert_ids
        assert "ALERT_WHATSAPP_PROFILE_MISSING" in alert_ids


def test_dashboard_snapshot_database_failure_resilience():
    """Verifies graceful snapshot fallback when database is completely down."""
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("DB Connection Lost")

    snapshot = DashboardService.get_dashboard_snapshot(mock_db)
    dto = DashboardSnapshotDTO(**snapshot)

    assert dto.database.connected is False
    assert dto.database.status == "UNAVAILABLE"
    assert dto.system_health.state == "UNHEALTHY"
    alert_ids = [a.id for a in dto.alerts]
    assert "ALERT_DB_DOWN" in alert_ids


def test_dashboard_snapshot_alerts_runner_and_cb(web_session):
    """Verifies alerts for tripped circuit breaker, stale leases, and runner degradation."""
    # Paused campaign for circuit breaker alert
    camp = Campaign(
        name="Paused Camp",
        message_template="Hello",
        status="PAUSED"
    )
    web_session.add(camp)
    web_session.commit()

    # Mock queue analytics with stale leases and runner states
    mock_q = {
        "backlog": {"queued": 0, "processing": 1, "retry_pending": 0, "stale_leases": 3},
        "terminal": {"confirmed_sends": 0, "failed": 0, "unknown_outcome": 0},
        "confirmed_throughput": {"last_1_hour": 0, "last_6_hours": 0, "last_24_hours": 0}
    }

    # Test 1: UNHEALTHY runner
    mock_runner = {
        "is_running": True,
        "state": "UNHEALTHY",
        "pid": 111,
        "campaign_id": camp.id,
        "heartbeat_age_seconds": 75.0,
    }

    with patch("app.services.analytics_service.AnalyticsService.get_queue_analytics", return_value=mock_q), \
         patch("app.web.services.runner_control_service.RunnerControlService.get_status", return_value=mock_runner):

        snapshot = DashboardService.get_dashboard_snapshot(web_session)
        dto = DashboardSnapshotDTO(**snapshot)
        alert_ids = [a.id for a in dto.alerts]
        assert "ALERT_CB_TRIPPED" in alert_ids
        assert "ALERT_STALE_LEASES" in alert_ids
        assert "ALERT_RUNNER_HEARTBEAT_STALE" in alert_ids

    # Test 2: DEGRADED runner
    mock_runner["state"] = "DEGRADED"
    mock_runner["heartbeat_age_seconds"] = 40.0
    with patch("app.services.analytics_service.AnalyticsService.get_queue_analytics", return_value=mock_q), \
         patch("app.web.services.runner_control_service.RunnerControlService.get_status", return_value=mock_runner):

        snapshot = DashboardService.get_dashboard_snapshot(web_session)
        dto = DashboardSnapshotDTO(**snapshot)
        alert_ids = [a.id for a in dto.alerts]
        assert "ALERT_RUNNER_HEARTBEAT_LAGGING" in alert_ids

    # Test 3: STALE_LOCK_DETECTED
    mock_runner["state"] = "STALE_LOCK_DETECTED"
    with patch("app.services.analytics_service.AnalyticsService.get_queue_analytics", return_value=mock_q), \
         patch("app.web.services.runner_control_service.RunnerControlService.get_status", return_value=mock_runner):

        snapshot = DashboardService.get_dashboard_snapshot(web_session)
        dto = DashboardSnapshotDTO(**snapshot)
        alert_ids = [a.id for a in dto.alerts]
        assert "ALERT_RUNNER_ORPHANED" in alert_ids
