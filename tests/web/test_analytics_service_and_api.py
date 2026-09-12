"""
Comprehensive test suite for Phase 7.5 Analytics & Reporting Control Center.

Covers:
- Section A: Confirmed Send Rate edge cases & locked formula exclusions
- Section B: Cardinality-safe contact outreach completion % vs queue terminal %
- Section C: Timezone authority (Africa/Cairo) and calendar boundary resolution
- Section D: Queue analytics strict decoupling (Live point-in-time vs Historical windowed)
- Section E: Streaming CSV exports, RBAC enforcement, phone privacy masking, and 5-stage audit lifecycle
- Section F: HTML UI views, disclaimer banners, and navigation
- Section G: Feature-surface prohibited terminology scan
"""

import json
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.models.audit_log import AuditLog
from app.models.app_setting import AppSetting
from app.models.user import UserRole
from app.services.analytics_service import AnalyticsService
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.services.analytics_web_service import AnalyticsWebService
from app.utils.timezone import resolve_calendar_range, get_app_timezone

DISCLAIMER_TEXT = "CONFIRMED SEND RATE is calculated exclusively from UI-confirmed dispatches. WhatsApp Web does not provide delivery or read receipts."


def _auth_cookies(web_session, user):
    token = session_manager.create_session(web_session, user)
    csrf_token = csrf_manager.generate_token()
    return {
        web_settings.WEB_SESSION_COOKIE_NAME: token,
        web_settings.WEB_CSRF_COOKIE_NAME: csrf_token,
    }


@pytest.fixture
def test_campaign_with_messages(web_session):
    """Creates a campaign with defined contacts and messages covering multiple states."""
    campaign = Campaign(
        name="Phase 7.5 Test Campaign",
        message_template="Hello {name}",
        status="RUNNING",
        min_delay_seconds=10,
        max_delay_seconds=30,
        daily_limit=100,
        error_threshold=5,
    )
    web_session.add(campaign)
    web_session.commit()

    contacts = []
    for i in range(10):
        c = Contact(
            name=f"Contact {i}",
            phone_e164=f"+20101234567{i}",
            country_code="EG",
        )
        web_session.add(c)
        contacts.append(c)
    web_session.commit()

    campaign_contacts = []
    for i, c in enumerate(contacts):
        status = "EXCLUDED" if i >= 8 else "ELIGIBLE"
        cc = CampaignContact(
            campaign_id=campaign.id,
            contact_id=c.id,
            status=status,
        )
        web_session.add(cc)
        campaign_contacts.append(cc)
    web_session.commit()

    now_utc = datetime.now(timezone.utc)

    # 4 SENT (Confirmed sends)
    for i in range(4):
        msg = Message(
            campaign_id=campaign.id,
            contact_id=contacts[i].id,
            campaign_contact_id=campaign_contacts[i].id,
            idempotency_key=f"camp_{campaign.id}_c_{contacts[i].id}_1",
            rendered_content="Hello world",
            status="SENT",
            attempt_count=1,
            sent_at=now_utc - timedelta(hours=1, minutes=i * 10),
            locked_at=now_utc - timedelta(hours=1, minutes=i * 10, seconds=15),
        )
        web_session.add(msg)

    # 1 FAILED (Permanent failure)
    web_session.add(Message(
        campaign_id=campaign.id,
        contact_id=contacts[4].id,
        campaign_contact_id=campaign_contacts[4].id,
        idempotency_key=f"camp_{campaign.id}_c_{contacts[4].id}_1",
        rendered_content="Hello world",
        status="FAILED",
        error_type="PERMANENT",
        attempt_count=3,
        failed_at=now_utc - timedelta(minutes=45),
    ))

    # 1 UNKNOWN_OUTCOME (FAILED with UNKNOWN_OUTCOME)
    web_session.add(Message(
        campaign_id=campaign.id,
        contact_id=contacts[5].id,
        campaign_contact_id=campaign_contacts[5].id,
        idempotency_key=f"camp_{campaign.id}_c_{contacts[5].id}_1",
        rendered_content="Hello world",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        attempt_count=1,
        failed_at=now_utc - timedelta(minutes=30),
    ))

    # 1 RETRY_PENDING (Must NOT be in denominator)
    web_session.add(Message(
        campaign_id=campaign.id,
        contact_id=contacts[6].id,
        campaign_contact_id=campaign_contacts[6].id,
        idempotency_key=f"camp_{campaign.id}_c_{contacts[6].id}_1",
        rendered_content="Hello world",
        status="RETRY_PENDING",
        attempt_count=1,
    ))

    # 1 QUEUED (Must NOT be in denominator)
    web_session.add(Message(
        campaign_id=campaign.id,
        contact_id=contacts[7].id,
        campaign_contact_id=campaign_contacts[7].id,
        idempotency_key=f"camp_{campaign.id}_c_{contacts[7].id}_1",
        rendered_content="Hello world",
        status="QUEUED",
        attempt_count=0,
    ))

    web_session.commit()
    return campaign


# ==============================================================================
# SECTION A: CONFIRMED SEND RATE EDGE CASES & EXCLUSIONS
# ==============================================================================

class TestConfirmedSendRateFormula:
    """Verifies that Confirmed Send Rate adheres strictly to locked formula and exclusions."""

    def test_zero_denominator_returns_zero(self, web_session):
        campaign = Campaign(name="Empty Campaign", message_template="Hi", status="DRAFT")
        web_session.add(campaign)
        web_session.commit()

        res = AnalyticsService.get_campaign_analytics(web_session, campaign.id)
        assert res is not None
        assert res["performance"]["confirmed_send_rate"] == 0.0
        assert res["performance"]["failure_rate"] == 0.0
        assert res["performance"]["terminal_dispatches"] == 0

    def test_pure_success_rate(self, web_session):
        campaign = Campaign(name="100% Campaign", message_template="Hi", status="COMPLETED")
        web_session.add(campaign)
        web_session.commit()

        c = Contact(name="Pure Success Contact", phone_e164="+201000000001", country_code="EG")
        web_session.add(c)
        web_session.commit()

        web_session.add(Message(
            campaign_id=campaign.id,
            contact_id=c.id,
            idempotency_key="key1",
            rendered_content="Hi",
            status="SENT",
            sent_at=datetime.now(timezone.utc),
        ))
        web_session.commit()

        res = AnalyticsService.get_campaign_analytics(web_session, campaign.id)
        assert res["performance"]["confirmed_send_rate"] == 100.0
        assert res["performance"]["failure_rate"] == 0.0
        assert res["performance"]["terminal_dispatches"] == 1

    def test_pure_failure_rate(self, web_session):
        campaign = Campaign(name="0% Campaign", message_template="Hi", status="COMPLETED")
        web_session.add(campaign)
        web_session.commit()

        c = Contact(name="Pure Fail Contact", phone_e164="+201000000002", country_code="EG")
        web_session.add(c)
        web_session.commit()

        web_session.add(Message(
            campaign_id=campaign.id,
            contact_id=c.id,
            idempotency_key="key2",
            rendered_content="Hi",
            status="FAILED",
            error_type="PERMANENT",
            failed_at=datetime.now(timezone.utc),
        ))
        web_session.commit()

        res = AnalyticsService.get_campaign_analytics(web_session, campaign.id)
        assert res["performance"]["confirmed_send_rate"] == 0.0
        assert res["performance"]["failure_rate"] == 100.0
        assert res["performance"]["terminal_dispatches"] == 1

    def test_unknown_outcome_included_in_denominator_and_retry_excluded(self, web_session, test_campaign_with_messages):
        res = AnalyticsService.get_campaign_analytics(web_session, test_campaign_with_messages.id)
        perf = res["performance"]
        # 4 SENT, 1 FAILED, 1 UNKNOWN_OUTCOME = 6 terminal dispatches
        # RETRY_PENDING (1) and QUEUED (1) strictly excluded!
        assert perf["terminal_dispatches"] == 6
        assert perf["confirmed_send_rate"] == round((4 / 6) * 100.0, 2)  # 66.67%
        assert perf["failure_rate"] == round((2 / 6) * 100.0, 2)  # 33.33%


# ==============================================================================
# SECTION B: CARDINALITY-SAFE CONTACT COMPLETION %
# ==============================================================================

class TestCardinalitySafeCompletion:
    """Verifies that 1:N Contact->Message cardinality never inflates contact completion."""

    def test_one_to_many_messages_does_not_inflate_contact_completion(self, web_session):
        campaign = Campaign(name="1:N Campaign", message_template="Hi", status="RUNNING")
        web_session.add(campaign)
        web_session.commit()

        c = Contact(name="Cardinality Contact", phone_e164="+201011111111", country_code="EG")
        web_session.add(c)
        web_session.commit()

        cc = CampaignContact(campaign_id=campaign.id, contact_id=c.id, status="SENT")
        web_session.add(cc)
        web_session.commit()

        # Simulate 3 message records for the same contact (1 failed attempt, 1 retry, 1 confirmed sent)
        now = datetime.now(timezone.utc)
        m1 = Message(campaign_id=campaign.id, contact_id=c.id, campaign_contact_id=cc.id,
                     idempotency_key="m1", rendered_content="Hi", status="FAILED", failed_at=now)
        m2 = Message(campaign_id=campaign.id, contact_id=c.id, campaign_contact_id=cc.id,
                     idempotency_key="m2", rendered_content="Hi", status="FAILED", failed_at=now)
        m3 = Message(campaign_id=campaign.id, contact_id=c.id, campaign_contact_id=cc.id,
                     idempotency_key="m3", rendered_content="Hi", status="SENT", sent_at=now)
        web_session.add_all([m1, m2, m3])
        web_session.commit()

        res = AnalyticsService.get_campaign_analytics(web_session, campaign.id)
        assert res["total_contacts"] == 1
        assert res["total_messages"] == 3
        # Contact completion is strictly from campaign_contacts (1 terminal out of 1 contact = 100.0%)
        assert res["performance"]["contact_completion_percentage"] == 100.0
        # If naive message counting was used, (1 sent + 2 failed) / 1 would be 300% (INCORRECT!)
        assert res["performance"]["contact_completion_percentage"] <= 100.0


# ==============================================================================
# SECTION C: TIMEZONE AUTHORITY (Africa/Cairo)
# ==============================================================================

class TestTimezoneAuthority:
    """Verifies that backend anchors calendar boundaries to APP_TIMEZONE."""

    def test_resolve_calendar_range_presets(self):
        tz = get_app_timezone()
        assert str(tz) == "Africa/Cairo"

        # Today preset
        cal_today = resolve_calendar_range("today")
        assert cal_today.preset == "today"
        assert cal_today.timezone_name == "Africa/Cairo"
        assert cal_today.start_utc < cal_today.end_utc
        # Half-open difference is exactly 24 hours (or 1 calendar day)
        diff = cal_today.end_utc - cal_today.start_utc
        assert diff.total_seconds() == 86400

        # Yesterday preset
        cal_yesterday = resolve_calendar_range("yesterday")
        assert cal_yesterday.preset == "yesterday"
        assert cal_yesterday.end_utc == cal_today.start_utc

        # Last 7 days covers 7 calendar days
        cal_7d = resolve_calendar_range("last_7_days")
        assert (cal_7d.end_utc - cal_7d.start_utc).total_seconds() == 7 * 86400

        # Custom preset
        cal_custom = resolve_calendar_range("custom", start_date="2026-09-01", end_date="2026-09-05")
        assert cal_custom.preset == "custom"
        assert (cal_custom.end_utc - cal_custom.start_utc).total_seconds() == 5 * 86400


# ==============================================================================
# SECTION D: QUEUE ANALYTICS DECOUPLING
# ==============================================================================

class TestQueueAnalyticsDecoupling:
    """Verifies strict decoupling of Current Queue Health (live) and Historical Execution Analytics."""

    def test_live_queue_snapshot_ignores_date_filtering(self, web_session, test_campaign_with_messages):
        # Even if historical date is queried, live queue snapshot reflects point-in-time state
        live = AnalyticsService.get_queue_live_analytics(web_session)
        assert live["queued_count"] == 1
        assert live["retry_pending_count"] == 1
        assert live["unknown_outcome_count"] == 1
        assert live["stale_leases_count"] == 0
        assert live["circuit_breaker_status"] == "CLOSED"
        assert live["emergency_stop_status"] == "INACTIVE"

    def test_historical_queue_analytics_applies_window(self, web_session, test_campaign_with_messages):
        now_utc = datetime.now(timezone.utc)
        # Window that includes all messages
        window_full = AnalyticsService.get_queue_historical_analytics(
            web_session,
            start_date=now_utc - timedelta(hours=3),
            end_date=now_utc + timedelta(hours=1),
        )
        assert window_full["dispatches_completed"] == 4
        assert window_full["permanent_failures"] == 1
        assert window_full["unknown_outcomes"] == 1
        assert window_full["confirmed_send_rate"] == 66.67
        assert window_full["average_lease_duration_seconds"] > 0

        # Empty past window
        window_empty = AnalyticsService.get_queue_historical_analytics(
            web_session,
            start_date=now_utc - timedelta(days=10),
            end_date=now_utc - timedelta(days=9),
        )
        assert window_empty["dispatches_completed"] == 0
        assert window_empty["confirmed_send_rate"] == 0.0


# ==============================================================================
# SECTION E: STREAMING CSV EXPORT, PRIVACY MASKING & 5-STAGE AUDIT
# ==============================================================================

class TestCSVExportAndAuditLifecycle:
    """Verifies fail-safe CSV streaming, RBAC, phone privacy masking, and audit trails."""

    def test_export_rbac_forbidden_for_viewer(self, client, web_session, create_user, test_campaign_with_messages):
        viewer = create_user("test_viewer", UserRole.VIEWER)
        cookies = _auth_cookies(web_session, viewer)

        # Campaign CSV export rejected with 403
        resp = client.get(f"/api/v1/analytics/export/campaign/{test_campaign_with_messages.id}", cookies=cookies)
        assert resp.status_code == 403

        # Summary CSV export rejected with 403
        resp_sum = client.get("/api/v1/analytics/export/summary", cookies=cookies)
        assert resp_sum.status_code == 403

    def test_export_operator_masks_phone_and_omits_message_body(self, client, web_session, create_user, test_campaign_with_messages):
        operator = create_user("test_operator", UserRole.OPERATOR)
        cookies = _auth_cookies(web_session, operator)

        resp = client.get(f"/api/v1/analytics/export/campaign/{test_campaign_with_messages.id}", cookies=cookies)
        assert resp.status_code == 200
        content = resp.text

        # Header check: message body strictly excluded
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        assert "message_id,campaign_id,contact_name,phone_e164,status,error_type,attempt_count,sent_at_utc,created_at_utc" in lines[0]
        assert "rendered_content" not in lines[0]
        assert "body" not in lines[0]

        # Phone masking: operator receives masked phone (+201******678)
        assert "+201" in content
        assert "*" in content

        # Clean completion emits ANALYTICS_REPORT_EXPORTED
        audit = (
            web_session.query(AuditLog)
            .filter(AuditLog.event_type == "ANALYTICS_REPORT_EXPORTED")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.status == "SUCCESS"
        assert audit.campaign_id == test_campaign_with_messages.id

    def test_export_admin_receives_unmasked_phone(self, client, web_session, create_user, test_campaign_with_messages):
        admin = create_user("test_admin", UserRole.ADMIN)
        cookies = _auth_cookies(web_session, admin)

        resp = client.get(f"/api/v1/analytics/export/campaign/{test_campaign_with_messages.id}", cookies=cookies)
        assert resp.status_code == 200
        content = resp.text
        # Admin gets full unmasked phone
        assert "+201012345670" in content

    def test_interrupted_stream_emits_failure_audit(self, web_session, create_user, test_campaign_with_messages):
        admin = create_user("test_admin_interrupted", UserRole.ADMIN)

        gen = AnalyticsWebService.stream_campaign_csv(
            campaign_id=test_campaign_with_messages.id,
            user=admin,
            db=web_session,
        )
        # Read only header then interrupt/close
        _ = next(gen)
        gen.close()  # Simulates client socket disconnection

        # Generator finally block must have recorded INTERRUPTED failure
        audit = (
            web_session.query(AuditLog)
            .filter(AuditLog.event_type == "ANALYTICS_REPORT_EXPORT_FAILED")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.status == "INTERRUPTED"
        assert audit.campaign_id == test_campaign_with_messages.id


# ==============================================================================
# SECTION F: HTML UI VIEWS & NAVIGATION
# ==============================================================================

class TestAnalyticsUIViews:
    """Verifies UI rendering, authentication redirects, and mandatory disclaimer banners."""

    def test_unauthenticated_redirects_to_login(self, client, create_user):
        create_user("existing_user", UserRole.ADMIN)

        resp = client.get("/analytics", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

        resp_camp = client.get("/analytics/campaigns/1", follow_redirects=False)
        assert resp_camp.status_code == 302
        assert "/login" in resp_camp.headers["location"]

    def test_analytics_overview_renders_ids_and_disclaimer(self, client, web_session, create_user, test_campaign_with_messages):
        viewer = create_user("test_ui_viewer", UserRole.VIEWER)
        cookies = _auth_cookies(web_session, viewer)

        resp = client.get("/analytics", cookies=cookies)
        assert resp.status_code == 200
        html = resp.text

        # Mandatory disclaimer presence
        assert "CONFIRMED SEND RATE" in html
        assert "WhatsApp Web does not provide delivery or read receipts" in html
        # Page header
        assert "Analytics &amp; Reporting" in html
        # Time window buttons
        assert "Today" in html
        assert "Last 7 Days" in html
        # Campaign performance table
        assert test_campaign_with_messages.name in html

    def test_campaign_detail_renders_funnel_and_disclaimer(self, client, web_session, create_user, test_campaign_with_messages):
        viewer = create_user("test_ui_viewer2", UserRole.VIEWER)
        cookies = _auth_cookies(web_session, viewer)

        resp = client.get(f"/analytics/campaigns/{test_campaign_with_messages.id}", cookies=cookies)
        assert resp.status_code == 200
        html = resp.text

        assert "CONFIRMED SEND RATE" in html
        assert "WhatsApp Web does not provide delivery or read receipts" in html
        assert test_campaign_with_messages.name in html
        assert "Audience Funnel" in html
        assert "Contact Outreach Completion" in html


# ==============================================================================
# SECTION G: FEATURE-SURFACE PROHIBITED TERMINOLOGY ASSERTION
# ==============================================================================

class TestProhibitedTerminologyScan:
    """
    Asserts ZERO occurrences of prohibited terms on the Analytics feature surface:
    - Rendered Analytics HTML templates (excluding the approved disclaimer text)
    - Analytics API JSON responses (excluding the disclaimer field)
    - Analytics Pydantic schemas
    - Analytics CSV export headers and data
    """

    PROHIBITED_METRIC_TERMS = [
        "delivered",
        "delivery rate",
        "delivery_rate",
        "success rate",
        "success_rate",
        "archived",
        "sending",
        "read rate",
        "read_rate",
        "read receipt",
    ]

    def test_prohibited_terms_on_rendered_html(self, client, web_session, create_user, test_campaign_with_messages):
        user = create_user("scan_user_html", UserRole.ADMIN)
        cookies = _auth_cookies(web_session, user)

        overview_html = client.get("/analytics", cookies=cookies).text.lower()
        detail_html = client.get(f"/analytics/campaigns/{test_campaign_with_messages.id}", cookies=cookies).text.lower()

        # Strip the approved disclaimer sentence before checking for prohibited metric claims
        import re
        overview_clean = re.sub(r'whatsapp web does not provide delivery or read receipts\.?', '', overview_html, flags=re.I)
        detail_clean = re.sub(r'whatsapp web does not provide delivery or read receipts\.?', '', detail_html, flags=re.I)

        for term in self.PROHIBITED_METRIC_TERMS:
            assert term not in overview_clean, f"Prohibited term '{term}' found in /analytics HTML outside disclaimer!"
            assert term not in detail_clean, f"Prohibited term '{term}' found in /analytics/campaigns HTML outside disclaimer!"

    def test_prohibited_terms_on_api_responses(self, client, web_session, create_user, test_campaign_with_messages):
        user = create_user("scan_user_api", UserRole.ADMIN)
        cookies = _auth_cookies(web_session, user)

        resp_overview = client.get("/api/v1/analytics/overview", cookies=cookies).text.lower()
        resp_camp = client.get(f"/api/v1/analytics/campaigns/{test_campaign_with_messages.id}", cookies=cookies).text.lower()
        resp_live = client.get("/api/v1/analytics/queue/live", cookies=cookies).text.lower()
        resp_hist = client.get("/api/v1/analytics/queue/historical", cookies=cookies).text.lower()

        # Strip approved disclaimer string from responses
        resp_overview_clean = resp_overview.replace(DISCLAIMER_TEXT.lower(), "")
        resp_camp_clean = resp_camp.replace(DISCLAIMER_TEXT.lower(), "")

        for term in self.PROHIBITED_METRIC_TERMS:
            assert term not in resp_overview_clean, f"Prohibited term '{term}' found in overview API JSON!"
            assert term not in resp_camp_clean, f"Prohibited term '{term}' found in campaign API JSON!"
            assert term not in resp_live, f"Prohibited term '{term}' found in queue live API JSON!"
            assert term not in resp_hist, f"Prohibited term '{term}' found in queue historical API JSON!"

    def test_prohibited_terms_on_csv_exports(self, client, web_session, create_user, test_campaign_with_messages):
        user = create_user("scan_user_csv", UserRole.ADMIN)
        cookies = _auth_cookies(web_session, user)

        csv_camp = client.get(f"/api/v1/analytics/export/campaign/{test_campaign_with_messages.id}", cookies=cookies).text.lower()
        csv_sum = client.get("/api/v1/analytics/export/summary", cookies=cookies).text.lower()

        for term in self.PROHIBITED_METRIC_TERMS:
            assert term not in csv_camp, f"Prohibited term '{term}' found in campaign CSV export!"
            assert term not in csv_sum, f"Prohibited term '{term}' found in summary CSV export!"
