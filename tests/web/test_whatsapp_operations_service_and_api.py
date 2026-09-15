"""
Tests for WhatsApp Operations & Session Control Center Web Service and REST API (Phase 7.6).

Verifies:
- GET /api/v1/whatsapp/status: 200 OK for all roles, sanitized response, runner coupling
- GET /api/v1/whatsapp/diagnostics: 200 OK, environmental checks, zero host paths
- GET /api/v1/whatsapp/commands/{request_id}: observable lifecycle, 404 for unknown
- POST /api/v1/whatsapp/health-check: role gating (OPERATOR+), CSRF, HTTP 409 conflict
- POST /api/v1/whatsapp/reconnect: role gating, CSRF, reason validation, HTTP 409 conflict
- POST /api/v1/whatsapp/disconnect: role gating, CSRF, reason validation
- POST /api/v1/whatsapp/logout: role gating (ADMIN+ only), CONFIRM-LOGOUT phrase validation
- POST /api/v1/whatsapp/commands/clear-stale: role gating (ADMIN+)
- Strict security verification: zero Worker host filesystem paths in API responses
"""

import json
import pytest
from app.models.user import UserRole
from app.models.app_setting import AppSetting
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.services.whatsapp_command_service import WhatsAppCommandService
from app.web.services.whatsapp_service import WhatsAppWebService


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


def test_get_whatsapp_status_unauthenticated(client):
    """Assert unauthenticated request to /status returns 401."""
    res = client.get("/api/v1/whatsapp/status")
    assert res.status_code == 401


def test_get_whatsapp_status_success_and_sanitization(client, web_session, create_user):
    """Verifies status endpoint for VIEWER role and strictly asserts zero path exposure."""
    viewer = create_user(username="viewer_user", role=UserRole.VIEWER)
    cookies, _ = _auth(web_session, viewer)

    res = client.get("/api/v1/whatsapp/status", cookies=cookies)
    assert res.status_code == 200
    data = res.json()["data"]

    # Verify standard fields
    assert "state" in data
    assert "health_state" in data
    assert "is_runner_active" in data
    assert "profile_present" in data
    assert "profile_storage_state" in data
    assert "disclaimer" in data
    assert "SEND_CONFIRMED" in data["disclaimer"]

    # Strict Path Sanitization Assertions:
    # Ensure raw host paths are NOT leaked in response
    payload_str = json.dumps(data)
    assert "profile_path" not in payload_str
    assert "/usr/bin/google-chrome" not in payload_str
    assert "./data/whatsapp_session" not in payload_str
    assert "google-chrome" not in payload_str or "version" in payload_str.lower()


def test_get_whatsapp_diagnostics_sanitized(client, web_session, create_user):
    """Verifies diagnostics endpoint and ensures zero host paths in check details."""
    operator = create_user(username="op_user", role=UserRole.OPERATOR)
    cookies, _ = _auth(web_session, operator)

    res = client.get("/api/v1/whatsapp/diagnostics", cookies=cookies)
    assert res.status_code == 200
    data = res.json()["data"]

    assert "overall_ready" in data
    assert "checks" in data
    assert len(data["checks"]) >= 3

    # Assert zero host filesystem paths in diagnostics details
    payload_str = json.dumps(data)
    assert "/usr/bin" not in payload_str
    assert "C:\\" not in payload_str
    assert "./data" not in payload_str


def test_get_command_by_id_endpoint(client, web_session, create_user):
    """Verifies observable command status inspection."""
    viewer = create_user(username="viewer_cmd", role=UserRole.VIEWER)
    cookies, _ = _auth(web_session, viewer)

    # 1. Non-existent command returns 404
    res_404 = client.get("/api/v1/whatsapp/commands/req_wa_nonexistent", cookies=cookies)
    assert res_404.status_code == 404

    # 2. Existing command returns 200 with lifecycle status
    _, _, cmd = WhatsAppCommandService.submit_command(
        db=web_session,
        action="HEALTH_CHECK",
        requested_by="tester",
    )
    req_id = cmd["request_id"]

    res_200 = client.get(f"/api/v1/whatsapp/commands/{req_id}", cookies=cookies)
    assert res_200.status_code == 200
    data = res_200.json()["data"]
    assert data["request_id"] == req_id
    assert data["action"] == "HEALTH_CHECK"
    assert data["status"] == "REQUESTED"


def test_health_check_endpoint_rbac_and_csrf(client, web_session, create_user):
    """Verifies RBAC and CSRF protection on POST /health-check."""
    viewer = create_user(username="viewer_hc", role=UserRole.VIEWER)
    operator = create_user(username="op_hc", role=UserRole.OPERATOR)

    viewer_cookies, viewer_headers = _auth(web_session, viewer)
    op_cookies, op_headers = _auth(web_session, operator)

    # 1. VIEWER gets 403 Forbidden
    res_viewer = client.post("/api/v1/whatsapp/health-check", cookies=viewer_cookies, headers=viewer_headers)
    assert res_viewer.status_code == 403

    # 2. OPERATOR missing CSRF gets 403
    res_no_csrf = client.post("/api/v1/whatsapp/health-check", cookies=op_cookies)
    assert res_no_csrf.status_code == 403

    # 3. OPERATOR with valid CSRF gets 200 OK
    res_ok = client.post("/api/v1/whatsapp/health-check", cookies=op_cookies, headers=op_headers)
    assert res_ok.status_code == 200
    data = res_ok.json()["data"]
    assert data["action"] == "HEALTH_CHECK"
    assert data["status"] == "REQUESTED"


def test_concurrent_command_submission_returns_409(client, web_session, create_user):
    """Verifies that concurrent command submission raises HTTP 409 Conflict."""
    operator = create_user(username="op_concurrent", role=UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    # First request: Health check
    res1 = client.post("/api/v1/whatsapp/health-check", cookies=cookies, headers=headers)
    assert res1.status_code == 200

    # Second request: Reconnect while health check is active -> HTTP 409 Conflict
    res2 = client.post(
        "/api/v1/whatsapp/reconnect",
        json={"reason": "Driver recovery test"},
        cookies=cookies,
        headers=headers,
    )
    assert res2.status_code == 409
    err_msg = res2.json().get("error", {}).get("message", "")
    assert "currently in progress" in err_msg


def test_reconnect_endpoint(client, web_session, create_user):
    """Verifies POST /reconnect with reason validation."""
    operator = create_user(username="op_rec", role=UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    # Missing reason -> 422 Unprocessable Entity
    res_bad = client.post("/api/v1/whatsapp/reconnect", json={}, cookies=cookies, headers=headers)
    assert res_bad.status_code == 422

    # Valid reason -> 200 OK
    res_ok = client.post(
        "/api/v1/whatsapp/reconnect",
        json={"reason": "Restarting browser after network timeout"},
        cookies=cookies,
        headers=headers,
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["data"]["action"] == "RECONNECT"


def test_disconnect_endpoint(client, web_session, create_user):
    """Verifies POST /disconnect."""
    operator = create_user(username="op_disc", role=UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    res_ok = client.post(
        "/api/v1/whatsapp/disconnect",
        json={"reason": "Scheduled host maintenance"},
        cookies=cookies,
        headers=headers,
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["data"]["action"] == "DISCONNECT"


def test_logout_endpoint_rbac_and_confirmation(client, web_session, create_user):
    """Verifies POST /logout requires ADMIN role and exact CONFIRM-LOGOUT phrase."""
    operator = create_user(username="op_logout", role=UserRole.OPERATOR)
    admin = create_user(username="admin_logout", role=UserRole.ADMIN)

    op_cookies, op_headers = _auth(web_session, operator)
    admin_cookies, admin_headers = _auth(web_session, admin)

    # 1. OPERATOR gets 403 Forbidden (requires ADMIN+)
    res_op = client.post(
        "/api/v1/whatsapp/logout",
        json={"confirm_phrase": "CONFIRM-LOGOUT", "clear_cache": False, "reason": "test"},
        cookies=op_cookies,
        headers=op_headers,
    )
    assert res_op.status_code == 403

    # 2. ADMIN with incorrect phrase gets 400 Bad Request
    res_bad_phrase = client.post(
        "/api/v1/whatsapp/logout",
        json={"confirm_phrase": "WRONG-PHRASE", "clear_cache": False, "reason": "test"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert res_bad_phrase.status_code == 400
    err_msg = res_bad_phrase.json().get("error", {}).get("message", "")
    assert "CONFIRM-LOGOUT" in err_msg

    # 3. ADMIN with exact phrase gets 200 OK
    res_ok = client.post(
        "/api/v1/whatsapp/logout",
        json={"confirm_phrase": "CONFIRM-LOGOUT", "clear_cache": True, "reason": "Re-pairing device"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["data"]["action"] == "LOGOUT"


def test_clear_stale_command_endpoint(client, web_session, create_user):
    """Verifies POST /commands/clear-stale allows ADMIN to clear stuck command."""
    operator = create_user(username="op_stale", role=UserRole.OPERATOR)
    admin = create_user(username="admin_stale", role=UserRole.ADMIN)

    op_cookies, op_headers = _auth(web_session, operator)
    admin_cookies, admin_headers = _auth(web_session, admin)

    # Submit a command
    WhatsAppCommandService.submit_command(web_session, action="HEALTH_CHECK", requested_by="user1")

    # OPERATOR cannot clear stale command -> 403
    res_op = client.post("/api/v1/whatsapp/commands/clear-stale", cookies=op_cookies, headers=op_headers)
    assert res_op.status_code == 403

    # ADMIN can clear stale command -> 200
    res_admin = client.post("/api/v1/whatsapp/commands/clear-stale", cookies=admin_cookies, headers=admin_headers)
    assert res_admin.status_code == 200
    assert res_admin.json()["data"]["cleared"] is True
