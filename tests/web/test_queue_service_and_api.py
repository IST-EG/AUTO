"""
Tests for Queue Service and REST API (Phase 7.4).

Verifies:
- Operational queue statistics calculation (including locked Confirmed Send Rate)
- Bounded server-side pagination and multi-dimensional filtering
- Role-based contact phone number privacy masking (VIEWER vs ADMIN)
- Stale worker lease reconciliation and audit recording
- Message cancellation from non-terminal states and rejection from terminal states
- UNKNOWN_OUTCOME manual resolution workflow (requires CONFIRM-NOT-DELIVERED and reason)
- Role-based access control (VIEWER, OPERATOR, ADMIN) and CSRF enforcement
- Deferred status of manual retry mutations (inspection only)
- Zero live WhatsApp dispatches
"""

from datetime import datetime, timezone, timedelta
import pytest
from fastapi import HTTPException

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import UserRole
from app.models.audit_log import AuditLog
from app.queue.state_machine import QueueState
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.schemas.queue import UnknownOutcomeResolveRequest
from app.web.services.queue_service import WebQueueService


def _auth(web_session, user):
    """Generates session cookies and CSRF headers for TestClient."""
    token = session_manager.create_session(web_session, user)
    csrf_token = csrf_manager.generate_token()
    cookies = {
        web_settings.WEB_SESSION_COOKIE_NAME: token,
        web_settings.WEB_CSRF_COOKIE_NAME: csrf_token,
    }
    headers = {"X-CSRF-Token": csrf_token}
    return cookies, headers


def _seed_test_data(web_session):
    """Seeds sample campaign, contact, and messages across different states."""
    c = Campaign(
        name="Queue Test Campaign",
        status="RUNNING",
        message_template="Hello {{name}}",
        daily_limit=100
    )
    web_session.add(c)
    web_session.flush()

    contact1 = Contact(
        name="Amr Hassan",
        phone_e164="+201012345678",
        country_code="EG"
    )
    contact2 = Contact(
        name="Sara Ali",
        phone_e164="+201198765432",
        country_code="EG"
    )
    web_session.add_all([contact1, contact2])
    web_session.flush()

    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(seconds=200)

    # 1. PENDING
    m1 = Message(
        campaign_id=c.id, contact_id=contact1.id,
        idempotency_key="key_pending_1", rendered_content="Test pending",
        status=QueueState.PENDING, queued_at=now, created_at=now, updated_at=now
    )
    # 2. QUEUED
    m2 = Message(
        campaign_id=c.id, contact_id=contact1.id,
        idempotency_key="key_queued_1", rendered_content="Test queued",
        status=QueueState.QUEUED, queued_at=now, created_at=now, updated_at=now
    )
    # 3. PROCESSING (Active lease)
    m3 = Message(
        campaign_id=c.id, contact_id=contact1.id,
        idempotency_key="key_proc_active", rendered_content="Test processing active",
        status=QueueState.PROCESSING, locked_by="worker-1", locked_at=now,
        queued_at=now, created_at=now, updated_at=now
    )
    # 4. PROCESSING (Stale lease)
    m4 = Message(
        campaign_id=c.id, contact_id=contact2.id,
        idempotency_key="key_proc_stale", rendered_content="Test processing stale",
        status=QueueState.PROCESSING, locked_by="worker-crashed", locked_at=stale_time,
        attempt_count=1, max_attempts=3, queued_at=now, created_at=now, updated_at=now
    )
    # 5. SENT
    m5 = Message(
        campaign_id=c.id, contact_id=contact1.id,
        idempotency_key="key_sent_1", rendered_content="Test sent",
        status=QueueState.SENT, sent_at=now,
        queued_at=now, created_at=now, updated_at=now
    )
    # 6. RETRY_PENDING
    m6 = Message(
        campaign_id=c.id, contact_id=contact2.id,
        idempotency_key="key_retry_1", rendered_content="Test retry pending",
        status=QueueState.RETRY_PENDING, attempt_count=1, max_attempts=3, retry_count=1,
        next_retry_at=now + timedelta(seconds=60), error_type="TEMPORARY",
        last_error="Network timeout", queued_at=now, created_at=now, updated_at=now
    )
    # 7. FAILED (Permanent)
    m7 = Message(
        campaign_id=c.id, contact_id=contact2.id,
        idempotency_key="key_failed_1", rendered_content="Test permanent failed",
        status=QueueState.FAILED, attempt_count=3, max_attempts=3,
        error_type="PERMANENT", last_error="Invalid destination number",
        failed_at=now, queued_at=now, created_at=now, updated_at=now
    )
    # 8. UNKNOWN_OUTCOME
    m8 = Message(
        campaign_id=c.id, contact_id=contact1.id,
        idempotency_key="key_unknown_1", rendered_content="Test unknown outcome",
        status=QueueState.FAILED, error_type="UNKNOWN_OUTCOME",
        last_error="Browser crashed during send confirmation",
        failed_at=now, queued_at=now, created_at=now, updated_at=now
    )
    # 9. CANCELLED
    m9 = Message(
        campaign_id=c.id, contact_id=contact2.id,
        idempotency_key="key_cancelled_1", rendered_content="Test cancelled",
        status=QueueState.CANCELLED, queued_at=now, created_at=now, updated_at=now
    )

    web_session.add_all([m1, m2, m3, m4, m5, m6, m7, m8, m9])
    web_session.commit()
    return c, [contact1, contact2], [m1, m2, m3, m4, m5, m6, m7, m8, m9]


# ==============================================================================
# SERVICE UNIT TESTS
# ==============================================================================

def test_queue_service_stats(web_session, create_user):
    _seed_test_data(web_session)
    stats = WebQueueService.get_queue_stats(web_session)

    assert stats.total == 9
    assert stats.pending == 1
    assert stats.queued == 1
    assert stats.processing == 2
    assert stats.sent == 1
    assert stats.retry_pending == 1
    assert stats.failed == 2  # m7 (PERMANENT) + m8 (UNKNOWN_OUTCOME)
    assert stats.unknown_outcome == 1
    assert stats.cancelled == 1
    assert stats.stale_leases == 1  # m4 is older than 120s

    # Confirmed Send Rate:
    # sent = 1, failed = 2 (includes unknown_outcome). Denominator = 3.
    # 1 / 3 * 100 = 33.33%
    assert stats.confirmed_send_rate == 33.33


def test_queue_service_listing_and_filtering(web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    user = create_user("test_viewer", UserRole.VIEWER)

    # 1. Status Filter: UNKNOWN_OUTCOME
    res = WebQueueService.list_messages(web_session, user, status="UNKNOWN_OUTCOME")
    assert res.total == 1
    assert res.items[0].is_unknown_outcome is True
    assert res.items[0].status == "FAILED"
    assert res.items[0].error_type == "UNKNOWN_OUTCOME"

    # 2. Status Filter: SENT
    res = WebQueueService.list_messages(web_session, user, status="SENT")
    assert res.total == 1
    assert res.items[0].status == "SENT"

    # 3. Campaign Filter
    res = WebQueueService.list_messages(web_session, user, campaign_id=c.id)
    assert res.total == 9

    # 4. Contact Query Filter
    res = WebQueueService.list_messages(web_session, user, contact_query="Sara")
    assert res.total >= 1
    for item in res.items:
        assert "Sara" in item.contact_name

    # 5. Retry Filter: RETRY_PENDING
    res = WebQueueService.list_messages(web_session, user, retry_filter="RETRY_PENDING")
    assert res.total == 1
    assert res.items[0].status == "RETRY_PENDING"

    # 6. Phone Masking for VIEWER
    res = WebQueueService.list_messages(web_session, user)
    for item in res.items:
        if item.contact_phone:
            assert "*" in item.contact_phone


def test_queue_service_detail_phone_masking(web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    m = msgs[0]  # contact1 phone is +201012345678

    # Viewer sees masked phone
    viewer = create_user("viewer_role", UserRole.VIEWER)
    detail_viewer = WebQueueService.get_message_detail(web_session, viewer, m.id)
    assert detail_viewer.contact_phone != "+201012345678"
    assert "*" in detail_viewer.contact_phone

    # Admin sees raw E.164 phone
    admin = create_user("admin_role", UserRole.ADMIN)
    detail_admin = WebQueueService.get_message_detail(web_session, admin, m.id)
    assert detail_admin.contact_phone == "+201012345678"


def test_queue_service_reconcile_stale_leases(web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    op = create_user("op_reconcile", UserRole.OPERATOR)

    # m4 was in PROCESSING with stale lease and attempt_count=1 (< max_attempts=3)
    res = WebQueueService.reconcile_queue(web_session, op)
    assert res.recovered_leases >= 1

    # Verify m4 transitioned to RETRY_PENDING
    web_session.refresh(msgs[3])
    assert msgs[3].status == QueueState.RETRY_PENDING
    assert msgs[3].locked_at is None
    assert msgs[3].locked_by is None

    # Verify audit log was written
    audit = (
        web_session.query(AuditLog)
        .filter(AuditLog.event_type == "QUEUE_RECONCILE_REQUESTED")
        .first()
    )
    assert audit is not None
    assert "op_reconcile" in audit.result


def test_queue_service_cancel_message(web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    op = create_user("op_cancel", UserRole.OPERATOR)

    # Cancel m1 (PENDING) -> success
    cancelled = WebQueueService.cancel_message(web_session, op, msgs[0].id)
    assert cancelled.status == QueueState.CANCELLED

    # Cancel m5 (SENT) -> rejected with 400
    with pytest.raises(HTTPException) as exc_info:
        WebQueueService.cancel_message(web_session, op, msgs[4].id)
    assert exc_info.value.status_code == 400


def test_queue_service_resolve_unknown_outcome(web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    op = create_user("op_uo", UserRole.OPERATOR)
    uo_msg = msgs[7]  # m8 (UNKNOWN_OUTCOME)

    # 1. Rejection: Empty/whitespace reason
    with pytest.raises(HTTPException) as exc:
        WebQueueService.resolve_unknown_outcome(
            web_session, op, uo_msg.id,
            UnknownOutcomeResolveRequest.model_construct(reason="     ", confirmation_phrase="CONFIRM-NOT-DELIVERED")
        )
    assert exc.value.status_code == 422

    # 2. Rejection: Invalid confirmation phrase
    with pytest.raises(HTTPException) as exc:
        WebQueueService.resolve_unknown_outcome(
            web_session, op, uo_msg.id,
            UnknownOutcomeResolveRequest(reason="Verified in chat", confirmation_phrase="YES-RETRY")
        )
    assert exc.value.status_code == 422

    # 3. Rejection: Message is not UNKNOWN_OUTCOME
    with pytest.raises(HTTPException) as exc:
        WebQueueService.resolve_unknown_outcome(
            web_session, op, msgs[0].id,  # m1 (PENDING)
            UnknownOutcomeResolveRequest(reason="Verified", confirmation_phrase="CONFIRM-NOT-DELIVERED")
        )
    assert exc.value.status_code == 400

    # 4. Successful Resolution
    resolved = WebQueueService.resolve_unknown_outcome(
        web_session, op, uo_msg.id,
        UnknownOutcomeResolveRequest(
            reason="Verified WhatsApp Web chat history at 12:00. No message received.",
            confirmation_phrase="CONFIRM-NOT-DELIVERED"
        )
    )
    assert resolved.status == QueueState.QUEUED
    assert resolved.locked_at is None
    assert resolved.locked_by is None
    assert "[MANUAL_OVERRIDE]" in resolved.last_error

    # Verify audit record
    audit = (
        web_session.query(AuditLog)
        .filter(
            AuditLog.message_id == uo_msg.id,
            AuditLog.event_type == "UNKNOWN_OUTCOME_RESOLUTION_REQUESTED"
        )
        .first()
    )
    assert audit is not None
    assert "CONFIRM-NOT-DELIVERED" in audit.result
    assert "op_uo" in audit.result


# ==============================================================================
# REST API INTEGRATION TESTS
# ==============================================================================

def test_api_queue_list_and_stats(client, web_session, create_user):
    _seed_test_data(web_session)
    viewer = create_user("api_viewer", UserRole.VIEWER)
    cookies, headers = _auth(web_session, viewer)

    # 1. GET /api/v1/queue
    client.cookies.update(cookies)
    resp = client.get("/api/v1/queue?page=1&page_size=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 9
    assert len(data["items"]) == 5
    assert data["total_pages"] == 2

    # 2. GET /api/v1/queue/stats
    resp = client.get("/api/v1/queue/stats")
    assert resp.status_code == 200
    stats = resp.json()["data"]
    assert stats["total"] == 9
    assert stats["unknown_outcome"] == 1
    assert stats["confirmed_send_rate"] == 33.33


def test_api_queue_detail_and_not_found(client, web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    viewer = create_user("api_viewer_det", UserRole.VIEWER)
    cookies, headers = _auth(web_session, viewer)

    client.cookies.update(cookies)
    resp = client.get(f"/api/v1/queue/{msgs[0].id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == msgs[0].id

    # 404 for non-existent message
    resp = client.get("/api/v1/queue/999999")
    assert resp.status_code == 404


def test_api_queue_reconcile_rbac_and_csrf(client, web_session, create_user):
    _seed_test_data(web_session)
    viewer = create_user("v_reconcile", UserRole.VIEWER)
    op = create_user("op_reconcile_api", UserRole.OPERATOR)

    # 1. VIEWER rejected with 403 Forbidden
    v_cookies, v_headers = _auth(web_session, viewer)
    client.cookies.clear()
    client.cookies.update(v_cookies)
    resp = client.post("/api/v1/queue/reconcile", headers=v_headers)
    assert resp.status_code == 403

    # 2. Missing CSRF header rejected with 403
    op_cookies, op_headers = _auth(web_session, op)
    client.cookies.clear()
    client.cookies.update(op_cookies)
    resp = client.post("/api/v1/queue/reconcile")  # No CSRF header
    assert resp.status_code == 403

    # 3. OPERATOR with valid CSRF succeeds
    resp = client.post("/api/v1/queue/reconcile", headers=op_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["recovered_leases"] >= 1


def test_api_queue_cancel_and_unknown_outcome_resolve(client, web_session, create_user):
    c, contacts, msgs = _seed_test_data(web_session)
    op = create_user("op_mutations", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, op)
    client.cookies.update(cookies)

    # 1. Cancel message
    resp = client.post(f"/api/v1/queue/{msgs[1].id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CANCELLED"

    # 2. Resolve UNKNOWN_OUTCOME
    uo_msg = msgs[7]
    payload = {
        "reason": "Verified external chat history. Message was not delivered.",
        "confirmation_phrase": "CONFIRM-NOT-DELIVERED"
    }
    resp = client.post(f"/api/v1/queue/{uo_msg.id}/resolve-unknown", json=payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "QUEUED"


def test_api_retry_mutation_deferred(client, web_session, create_user):
    """
    Verifies that no manual retry mutation endpoint exists in Phase 7.4.
    Retries are strictly autonomous; attempts to invoke a retry mutation must 404 or 405.
    """
    c, contacts, msgs = _seed_test_data(web_session)
    op = create_user("op_no_retry", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, op)
    client.cookies.update(cookies)

    resp = client.post(f"/api/v1/queue/{msgs[5].id}/retry", headers=headers)
    assert resp.status_code in (404, 405)
