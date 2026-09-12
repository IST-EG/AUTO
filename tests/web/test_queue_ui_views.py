"""
UI Views integration tests for Phase 7.4 Queue & Message Operations.

Verifies:
- /queue list view renders with 200 OK for authenticated users
- /queue redirects to /login for unauthenticated requests
- /queue/{message_id} detail view renders with 200 OK
- /queue/{message_id} displays prominent UNKNOWN_OUTCOME warning and resolution button
- /queue/{message_id} redirects to /login for unauthenticated requests
"""

from datetime import datetime, timezone
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import UserRole
from app.queue.state_machine import QueueState
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager


def _auth(web_session, user):
    token = session_manager.create_session(web_session, user)
    csrf_token = csrf_manager.generate_token()
    cookies = {
        web_settings.WEB_SESSION_COOKIE_NAME: token,
        web_settings.WEB_CSRF_COOKIE_NAME: csrf_token,
    }
    return cookies


def test_queue_ui_unauthenticated_redirects(client):
    # /queue redirects to /setup or /login
    resp = client.get("/queue", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location", "") in ("/login", "/setup")

    # /queue/1 redirects to /setup or /login
    resp = client.get("/queue/1", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location", "") in ("/login", "/setup")


def test_queue_ui_authenticated_views(client, web_session, create_user):
    # Setup test data
    c = Campaign(name="UI Queue Campaign", status="RUNNING", message_template="Hello")
    web_session.add(c)
    web_session.flush()

    contact = Contact(name="Hany Zaki", phone_e164="+201009876543", country_code="EG")
    web_session.add(contact)
    web_session.flush()

    now = datetime.now(timezone.utc)
    m_queued = Message(
        campaign_id=c.id, contact_id=contact.id,
        idempotency_key="ui_key_1", rendered_content="Rendered content test",
        status=QueueState.QUEUED, queued_at=now, created_at=now, updated_at=now
    )
    m_uo = Message(
        campaign_id=c.id, contact_id=contact.id,
        idempotency_key="ui_key_uo", rendered_content="Rendered UNKNOWN_OUTCOME",
        status=QueueState.FAILED, error_type="UNKNOWN_OUTCOME",
        last_error="Browser connection lost during dispatch confirmation",
        failed_at=now, queued_at=now, created_at=now, updated_at=now
    )
    web_session.add_all([m_queued, m_uo])
    web_session.commit()

    user = create_user("queue_ui_user", UserRole.OPERATOR)
    cookies = _auth(web_session, user)
    client.cookies.update(cookies)

    # 1. Test /queue renders
    resp = client.get("/queue")
    assert resp.status_code == 200
    assert "Queue &amp; Message Operations" in resp.text or "Queue & Message Operations" in resp.text
    assert "Reconcile Stale Leases" in resp.text
    assert "Rendered content test" not in resp.text  # message body not in list table
    assert "#" + str(m_queued.id) in resp.text

    # 2. Test /queue/{id} renders standard message
    resp = client.get(f"/queue/{m_queued.id}")
    assert resp.status_code == 200
    assert f"Message #{m_queued.id}" in resp.text
    assert "Rendered Message Body" in resp.text
    assert "Rendered content test" in resp.text
    assert "Cancel Message" in resp.text

    # 3. Test /queue/{id} renders UNKNOWN_OUTCOME warning and resolve button
    resp = client.get(f"/queue/{m_uo.id}")
    assert resp.status_code == 200
    assert "UNKNOWN OUTCOME — MANUAL VERIFICATION REQUIRED" in resp.text
    assert "Resolve as Not Sent" in resp.text
    assert "CONFIRM-NOT-DELIVERED" in resp.text
