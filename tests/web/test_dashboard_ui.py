"""
Tests for Dashboard UI template and HTML rendering.

Verifies:
- Unauthenticated access redirects to /setup (when no users exist) or /login (when initialized)
- Setup bootstrap detection redirects to /setup
- Authenticated operator access renders complete Operational Control Center
- Strict terminology enforcement (CONFIRMED SEND RATE, UNKNOWN OUTCOME)
- Modal dialogs and safe control action presence
"""

import pytest

from app.models.user import UserRole
from app.web.config import web_settings
from app.web.security.session import session_manager


def test_dashboard_ui_unauthenticated_redirects_to_setup_when_no_users(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/setup" in resp.headers["location"]


def test_dashboard_ui_unauthenticated_redirects_to_login_when_users_exist(client, create_user):
    create_user("setup_owner", UserRole.OWNER)
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_dashboard_ui_authenticated_renders_controls(client, web_session, create_user):
    user = create_user("dashboard_op", UserRole.OPERATOR)
    token = session_manager.create_session(web_session, user)

    resp = client.get(
        "/dashboard",
        cookies={web_settings.WEB_SESSION_COOKIE_NAME: token}
    )
    assert resp.status_code == 200
    html = resp.text

    # Verify structural elements
    assert "Operational Control Center" in html
    assert "CONFIRMED SEND RATE" in html
    assert "Subsystem Telemetry" in html
    assert "Safe Runner Control" in html
    assert "Emergency Controls" in html
    assert "Queue Breakdown" in html
    assert "UNKNOWN OUTCOME" in html

    # Verify user context rendered
    assert "dashboard_op" in html
    assert "OPERATOR" in html

    # Verify modal dialogs are present
    assert "modal-start-runner" in html
    assert "modal-stop-runner" in html
    assert "modal-emergency-stop" in html
    assert "modal-emergency-resume" in html

    # Verify control buttons
    assert "btn-start-runner" in html
    assert "btn-stop-runner" in html
    assert "btn-emergency-stop" in html
    assert "btn-emergency-resume" in html
