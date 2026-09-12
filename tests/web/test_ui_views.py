"""
Tests for HTML UI Pages and View Routing.

Verifies:
- /setup available with zero users, redirects to /login once user exists
- /login rendered for unauthenticated visitors, redirects to /dashboard if logged in
- /dashboard requires authentication, renders username and role badge
- /logout clears cookies and redirects to /login
- Root / route redirects to appropriate page based on system state
"""

from app.models.user import UserRole
from app.web.config import web_settings
from app.web.security.session import session_manager


def test_ui_index_redirects_to_setup_when_no_users(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/setup"


def test_ui_setup_page_renders_with_zero_users(client):
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "Initial System Setup" in resp.text
    assert "Create OWNER Account" in resp.text


def test_ui_setup_page_redirects_to_login_when_user_exists(client, create_user):
    create_user(username="existing_admin", role=UserRole.ADMIN)

    resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


def test_ui_login_page_renders_unauthenticated(client, create_user):
    create_user(username="existing_admin", role=UserRole.ADMIN)

    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Sign In" in resp.text
    assert "Password" in resp.text


def test_ui_dashboard_redirects_unauthenticated_to_login(client, create_user):
    create_user(username="existing_admin", role=UserRole.ADMIN)

    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


def test_ui_dashboard_renders_authenticated(client, create_user, web_session):
    user = create_user(username="dashboard_operator", role=UserRole.OPERATOR)
    token = session_manager.create_session(web_session, user)

    resp = client.get("/dashboard", cookies={web_settings.WEB_SESSION_COOKIE_NAME: token})
    assert resp.status_code == 200
    assert "Operational Control Center" in resp.text
    assert "dashboard_operator" in resp.text
    assert "OPERATOR" in resp.text


def test_ui_logout_view(client, create_user, web_session):
    user = create_user(username="logout_ui_user", role=UserRole.OPERATOR)
    token = session_manager.create_session(web_session, user)

    resp = client.get(
        "/logout",
        cookies={web_settings.WEB_SESSION_COOKIE_NAME: token},
        follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


def test_ui_login_redirects_authenticated_to_dashboard(client, create_user, web_session):
    user = create_user(username="logged_in_user", role=UserRole.OPERATOR)
    token = session_manager.create_session(web_session, user)

    resp = client.get("/login", cookies={web_settings.WEB_SESSION_COOKIE_NAME: token}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/dashboard"
