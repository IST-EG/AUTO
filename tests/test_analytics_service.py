"""
Unit tests for AnalyticsService covering campaign metrics, queue aggregations,
runner state, provider distribution, and the authoritative Confirmed Send Rate formula.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.queue.state_machine import QueueState
from app.runner.process_lock import ProcessLock
from app.services.analytics_service import AnalyticsService


@pytest.fixture
def sample_analytics_campaign(db_session):
    """Creates a sample campaign with contacts and messages across all lifecycle states."""
    campaign = Campaign(
        name="Analytics Test Campaign",
        message_template="Hello {name}, promo message",
        status="RUNNING",
        scheduled_start_at=datetime.now(timezone.utc) - timedelta(hours=2),
        min_delay_seconds=5,
        max_delay_seconds=15,
        daily_limit=50,
        error_threshold=5,
    )
    db_session.add(campaign)
    db_session.commit()

    # Create 10 contacts: 8 eligible, 2 excluded
    contacts = []
    for i in range(10):
        c = Contact(
            phone_e164=f"+20101234567{i}",
            country_code="EG",
            name=f"Contact {i}",
        )
        db_session.add(c)
        contacts.append(c)
    db_session.commit()

    # Link contacts to campaign
    campaign_contacts = []
    for i, c in enumerate(contacts):
        status = "EXCLUDED" if i >= 8 else "ELIGIBLE"
        cc = CampaignContact(
            campaign_id=campaign.id,
            contact_id=c.id,
            status=status,
        )
        db_session.add(cc)
        campaign_contacts.append(cc)
    db_session.commit()

    # Create messages for eligible contacts:
    # 4 confirmed sends (SENT)
    # 1 failed (FAILED, error_type=PERMANENT)
    # 1 unknown outcome (FAILED, error_type=UNKNOWN_OUTCOME)
    # 1 retry pending (RETRY_PENDING)
    # 1 queued (QUEUED)
    now_utc = datetime.now(timezone.utc)

    # 4 SENT
    for i in range(4):
        msg = Message(
            campaign_id=campaign.id,
            contact_id=contacts[i].id,
            campaign_contact_id=campaign_contacts[i].id,
            idempotency_key=f"camp_{campaign.id}_contact_{contacts[i].id}_1",
            rendered_content="Hello, promo message",
            status="SENT",
            attempt_count=1,
            sent_at=now_utc - timedelta(minutes=30 * (i + 1)),
            locked_at=now_utc - timedelta(minutes=31 * (i + 1)),
        )
        db_session.add(msg)

    # 1 FAILED
    msg_fail = Message(
        campaign_id=campaign.id,
        contact_id=contacts[4].id,
        campaign_contact_id=campaign_contacts[4].id,
        idempotency_key=f"camp_{campaign.id}_contact_{contacts[4].id}_1",
        rendered_content="Hello, promo message",
        status="FAILED",
        error_type="PERMANENT",
        attempt_count=3,
        failed_at=now_utc - timedelta(minutes=15),
    )
    db_session.add(msg_fail)

    # 1 UNKNOWN_OUTCOME
    msg_unknown = Message(
        campaign_id=campaign.id,
        contact_id=contacts[5].id,
        campaign_contact_id=campaign_contacts[5].id,
        idempotency_key=f"camp_{campaign.id}_contact_{contacts[5].id}_1",
        rendered_content="Hello, promo message",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        attempt_count=1,
        last_error="Browser crash during checkmark wait",
    )
    db_session.add(msg_unknown)

    # 1 RETRY_PENDING
    msg_retry = Message(
        campaign_id=campaign.id,
        contact_id=contacts[6].id,
        campaign_contact_id=campaign_contacts[6].id,
        idempotency_key=f"camp_{campaign.id}_contact_{contacts[6].id}_1",
        rendered_content="Hello, promo message",
        status="RETRY_PENDING",
        attempt_count=1,
    )
    db_session.add(msg_retry)

    # 1 QUEUED
    msg_queued = Message(
        campaign_id=campaign.id,
        contact_id=contacts[7].id,
        campaign_contact_id=campaign_contacts[7].id,
        idempotency_key=f"camp_{campaign.id}_contact_{contacts[7].id}_1",
        rendered_content="Hello, promo message",
        status="QUEUED",
        attempt_count=0,
    )
    db_session.add(msg_queued)

    db_session.commit()
    return campaign


class TestAnalyticsService:
    """Test suite for AnalyticsService aggregations."""

    def test_campaign_analytics_formula_and_exclusions(self, db_session, sample_analytics_campaign):
        analytics = AnalyticsService.get_campaign_analytics(db_session, sample_analytics_campaign.id)

        assert analytics is not None
        assert analytics["campaign_id"] == sample_analytics_campaign.id
        assert analytics["total_contacts"] == 10
        assert analytics["contacts_breakdown"]["eligible"] == 8
        assert analytics["contacts_breakdown"]["excluded"] == 2

        queue_b = analytics["queue_breakdown"]
        assert queue_b["confirmed_sends"] == 4
        assert queue_b["failed"] == 1
        assert queue_b["unknown_outcome"] == 1
        assert queue_b["retry_pending"] == 1
        assert queue_b["queued"] == 1

        perf = analytics["performance"]
        # Formula verification:
        # confirmed_sends = 4
        # terminal_denominator = 4 (confirmed) + 1 (failed) + 1 (unknown) = 6
        # Note: RETRY_PENDING (1) and QUEUED (1) are EXCLUDED from denominator!
        # confirmed_send_rate = (4 / 6) * 100 = 66.67%
        assert perf["terminal_dispatches"] == 6
        assert perf["confirmed_send_rate"] == 66.67
        # failure_rate = (1 + 1) / 6 * 100 = 33.33%
        assert perf["failure_rate"] == 33.33

        # Completion percentage:
        # terminal contacts: 4 (sent) + 1 (failed) + 1 (unknown) + 2 (excluded) = 8
        # total contacts = 10 -> 80.0%
        assert perf["completion_percentage"] == 80.0

        # Duration verification
        assert analytics["duration_seconds"] > 0

    def test_campaign_not_found(self, db_session):
        analytics = AnalyticsService.get_campaign_analytics(db_session, 999999)
        assert analytics is None

    def test_queue_analytics(self, db_session, sample_analytics_campaign):
        queue_data = AnalyticsService.get_queue_analytics(db_session, sample_analytics_campaign.id)

        assert queue_data["backlog"]["queued"] == 1
        assert queue_data["backlog"]["retry_pending"] == 1
        assert queue_data["terminal"]["confirmed_sends"] == 4
        assert queue_data["terminal"]["failed"] == 1
        assert queue_data["terminal"]["unknown_outcome"] == 1

        # Health warnings
        assert queue_data["health_warnings"]["has_unknown_outcome"] is True
        assert queue_data["health_warnings"]["has_stale_leases"] is False

        # Throughput
        assert queue_data["confirmed_throughput"]["last_24_hours"] == 4

    def test_runner_analytics_stopped(self, db_session):
        runner_data = AnalyticsService.get_runner_analytics(db_session)
        assert runner_data["is_running"] is False
        assert runner_data["state"] == "STOPPED"
        assert runner_data["pid"] is None

    def test_provider_analytics(self, db_session, sample_analytics_campaign):
        provider_data = AnalyticsService.get_provider_analytics(db_session)
        assert provider_data["provider_name"] == "WhatsAppWebProvider"
        assert "session" in provider_data
        dist = provider_data["dispatch_distribution"]
        assert dist["confirmed_sends"] == 4
        assert dist["permanent_failures"] == 1
        assert dist["unknown_outcomes"] == 1

    def test_system_analytics(self, db_session, sample_analytics_campaign):
        system_data = AnalyticsService.get_system_analytics(db_session)
        assert "emergency_stop" in system_data
        assert system_data["emergency_stop"]["active"] is False
        assert system_data["campaigns"]["running"] >= 1
        assert system_data["queue_summary"]["depth"] >= 1
        assert "daily_quota" in system_data
        assert system_data["daily_quota"]["sent_today"] >= 4

    def test_runner_analytics_active(self, db_session):
        mock_info = {
            "pid": 12345,
            "worker_id": "worker-unit-test",
            "campaign_id": 99,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "sent_count": 42,
        }
        with patch.object(ProcessLock, "get_active_runner_info", return_value=mock_info), \
             patch("app.services.analytics_service.is_pid_alive", return_value=True):
            data = AnalyticsService.get_runner_analytics(db_session)
            assert data["is_running"] is True
            assert data["state"] == "RUNNING"
            assert data["pid"] == 12345
            assert data["dispatches_completed"] == 42
            assert data["is_stale"] is False

    def test_campaign_analytics_with_terminal_and_zero_denominator(self, db_session):
        camp = Campaign(
            name="Completed Campaign",
            message_template="Hello",
            status="COMPLETED",
            scheduled_start_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            scheduled_end_at=datetime.now(timezone.utc),
        )
        db_session.add(camp)
        db_session.flush()

        msg_proc = Message(
            campaign_id=camp.id,
            contact_id=1,
            rendered_content="Hello",
            status=QueueState.PROCESSING,
            idempotency_key="msg_proc",
        )
        msg_skip = Message(
            campaign_id=camp.id,
            contact_id=1,
            rendered_content="Hello",
            status=QueueState.SKIPPED,
            idempotency_key="msg_skip",
        )
        msg_canc = Message(
            campaign_id=camp.id,
            contact_id=1,
            rendered_content="Hello",
            status=QueueState.CANCELLED,
            idempotency_key="msg_canc",
        )
        db_session.add_all([msg_proc, msg_skip, msg_canc])
        db_session.commit()

        data = AnalyticsService.get_campaign_analytics(db_session, camp.id)
        assert data["queue_breakdown"]["processing"] == 1
        assert data["queue_breakdown"]["skipped"] == 1
        assert data["queue_breakdown"]["cancelled"] == 1
        assert data["duration_seconds"] > 0
        assert data["performance"]["confirmed_send_rate"] == 0.0

    def test_global_queue_analytics(self, db_session):
        data = AnalyticsService.get_queue_analytics(db_session, campaign_id=None)
        assert data["campaign_id"] is None
        assert "backlog" in data

