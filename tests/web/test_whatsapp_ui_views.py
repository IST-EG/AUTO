"""
Tests for WhatsApp Operations & Session Control UI Views (Phase 7.6).

Verifies:
- GET /whatsapp: 200 OK for authenticated user
- Redirect to /login for unauthenticated request
- Mandatory semantic disclaimer banner is present in rendered HTML
- Complete absence of "Emergency Disconnect" (must NOT exist)
- Presence of "Controlled Disconnect"
- Zero Worker host filesystem paths in rendered HTML
- Active navigation item highlighting on sidebar
"""

import pytest
from app.models.user import UserRole
from app.web.config import web_settings
from app.web.security.session import session_manager


def _auth_cookie(web_session, user):
    token = session_manager.create_session(web_session, user)
    return {web_settings.WEB_SESSION_COOKIE_NAME: token}


def test_whatsapp_ui_view_unauthenticated(client):
    """Assert unauthenticated request to /whatsapp redirects to /login."""
    res = client.get("/whatsapp", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers.get("location", "") in ("/login", "/setup")


def test_whatsapp_ui_view_authenticated(client, web_session, create_user):
    """Assert authenticated request to /whatsapp renders 200 with IDS template."""
    user = create_user(username="viewer_ui", role=UserRole.VIEWER)
    cookies = _auth_cookie(web_session, user)

    res = client.get("/whatsapp", cookies=cookies)
    assert res.status_code == 200
    html = res.text

    # 1. Title & Header
    assert "WhatsApp Operations" in html
    assert "Provider lifecycle supervision" in html

    # 2. Mandatory Semantic Disclaimer Banner
    assert "Semantic Boundary Guarantee:" in html
    assert "SEND_CONFIRMED represents WhatsApp Web UI send confirmation only" in html

    # 3. Complete ABSENCE of "Emergency Disconnect"
    assert "Emergency Disconnect" not in html

    # 4. Presence of "Controlled Disconnect"
    assert "Controlled Disconnect" in html

    # 5. Strict Host Path Sanitization Assertions
    assert "/usr/bin/google-chrome" not in html
    assert "./data/whatsapp_session" not in html
    assert "C:\\Users\\" not in html

    # 6. Sidebar active link check
    assert 'href="/whatsapp"' in html
    assert 'class="sidebar-link active"' in html
