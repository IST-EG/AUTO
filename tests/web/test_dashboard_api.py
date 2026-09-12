"""
Tests for Dashboard, Runner, and System Control REST APIs.

Verifies:
- 401 Unauthorized for unauthenticated callers
- Role-based authorization matrix (VIEWER, OPERATOR, ADMIN, OWNER)
- CSRF double-submit protection on all mutation endpoints
- Safe runner start / stop endpoints and conflict handling
- Emergency stop and resume lifecycle enforcement
"""

import pytest
from unittest.mock import patch, MagicMock

from app.models.user import UserRole
from app.models.campaign import Campaign
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.services.runner_control_service import RunnerControlService
from app.scheduler.emergency_stop import EmergencyStop


def _auth_headers_and_cookies(web_session, user):
    token = session_manager.create_session(web_session, user)
    csrf_token = csrf_manager.generate_token()
    cookies = {
        web_settings.WEB_SESSION_COOKIE_NAME: token,
        web_settings.WEB_CSRF_COOKIE_NAME: csrf_token
    }
    headers = {
        "X-CSRF-Token": csrf_token
    }
    return cookies, headers


def test_dashboard_endpoints_unauthenticated(client):
    assert client.get("/api/v1/dashboard/summary").status_code == 401
    assert client.get("/api/v1/dashboard/health").status_code == 401
    assert client.get("/api/v1/dashboard/queue").status_code == 401
    assert client.get("/api/v1/dashboard/campaign").status_code == 401
    assert client.get("/api/v1/dashboard/runner").status_code == 401


def test_dashboard_summary_authenticated_viewer(client, web_session, create_user):
    user = create_user("testviewer", UserRole.VIEWER)
    cookies, _ = _auth_headers_and_cookies(web_session, user)

    resp = client.get("/api/v1/dashboard/summary", cookies=cookies)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["error"] is None
    data = payload["data"]
    assert "system_health" in data
    assert "database" in data
    assert "queue" in data
    assert "runner" in data
    assert "alerts" in data


def test_dashboard_sub_endpoints(client, web_session, create_user):
    user = create_user("testsubendpoints", UserRole.VIEWER)
    cookies, _ = _auth_headers_and_cookies(web_session, user)

    r_health = client.get("/api/v1/dashboard/health", cookies=cookies)
    assert r_health.status_code == 200
    assert "state" in r_health.json()["data"]

    r_queue = client.get("/api/v1/dashboard/queue", cookies=cookies)
    assert r_queue.status_code == 200
    assert "queued" in r_queue.json()["data"]

    r_camp = client.get("/api/v1/dashboard/campaign", cookies=cookies)
    assert r_camp.status_code == 200

    r_runner = client.get("/api/v1/dashboard/runner", cookies=cookies)
    assert r_runner.status_code == 200
    assert "state" in r_runner.json()["data"]


def test_runner_status_endpoint(client, web_session, create_user):
    user = create_user("testop", UserRole.OPERATOR)
    cookies, _ = _auth_headers_and_cookies(web_session, user)

    resp = client.get("/api/v1/runner/status", cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "state" in data
    assert "is_running" in data


def test_runner_start_rbac_viewer_forbidden(client, web_session, create_user):
    viewer = create_user("viewer_user", UserRole.VIEWER)
    cookies, headers = _auth_headers_and_cookies(web_session, viewer)

    resp = client.post(
        "/api/v1/runner/start",
        json={"campaign_id": 1},
        cookies=cookies,
        headers=headers
    )
    assert resp.status_code == 403


def test_runner_start_csrf_missing(client, web_session, create_user):
    operator = create_user("operator_user", UserRole.OPERATOR)
    cookies, _ = _auth_headers_and_cookies(web_session, operator)

    # Missing X-CSRF-Token header
    resp = client.post(
        "/api/v1/runner/start",
        json={"campaign_id": 1},
        cookies=cookies
    )
    assert resp.status_code == 403


def test_runner_start_conflict(client, web_session, create_user):
    operator = create_user("op_lock_conflict", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(
        RunnerControlService,
        "start_runner",
        return_value=(False, "Cannot start runner: another production runner process is already running.", None)
    ):
        resp = client.post(
            "/api/v1/runner/start",
            json={"campaign_id": 1},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 409
        assert "already running" in resp.json()["error"]["message"]


def test_runner_start_success(client, web_session, create_user):
    operator = create_user("op_start_success", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(
        RunnerControlService,
        "start_runner",
        return_value=(True, "Production runner successfully initiated.", {"campaign_id": 1, "pid": 7777, "started_at": "now"})
    ):
        resp = client.post(
            "/api/v1/runner/start",
            json={"campaign_id": 1},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["pid"] == 7777


def test_runner_stop_failure(client, web_session, create_user):
    operator = create_user("op_stop_fail", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(
        RunnerControlService,
        "stop_runner",
        return_value=(False, "No active runner process detected.", None)
    ):
        resp = client.post(
            "/api/v1/runner/stop",
            json={"timeout_seconds": 5},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 400
        assert "No active runner" in resp.json()["error"]["message"]


def test_runner_stop_success(client, web_session, create_user):
    operator = create_user("op_stop_success", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(
        RunnerControlService,
        "stop_runner",
        return_value=(True, "Runner process 7777 terminated successfully.", {"pid": 7777, "stopped_at": "now"})
    ):
        resp = client.post(
            "/api/v1/runner/stop",
            json={"timeout_seconds": 5},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["pid"] == 7777


def test_emergency_status_endpoint(client, web_session, create_user):
    user = create_user("estop_status_user", UserRole.VIEWER)
    cookies, _ = _auth_headers_and_cookies(web_session, user)

    resp = client.get("/api/v1/system/emergency-status", cookies=cookies)
    assert resp.status_code == 200
    assert "is_active" in resp.json()["data"]


def test_emergency_stop_viewer_rejected(client, web_session, create_user):
    viewer = create_user("estop_viewer", UserRole.VIEWER)
    cookies, headers = _auth_headers_and_cookies(web_session, viewer)

    resp = client.post(
        "/api/v1/system/emergency-stop",
        json={"reason": "Test incident"},
        cookies=cookies,
        headers=headers
    )
    assert resp.status_code == 403


def test_emergency_stop_success(client, web_session, create_user):
    operator = create_user("estop_op", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(EmergencyStop, "trigger") as mock_trigger:
        resp = client.post(
            "/api/v1/system/emergency-stop",
            json={"reason": "Manual operator intervention"},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_active"] is True
        assert data["status"] == "ACTIVE (HALTED)"
        mock_trigger.assert_called_once_with(reason="Manual operator intervention")


def test_emergency_resume_operator_forbidden(client, web_session, create_user):
    # Operator is NOT allowed to resume - requires ADMIN or OWNER
    operator = create_user("estop_op_resume", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    resp = client.post(
        "/api/v1/system/emergency-resume",
        json={"reason": "Issue cleared"},
        cookies=cookies,
        headers=headers
    )
    assert resp.status_code == 403


def test_emergency_resume_admin_success(client, web_session, create_user):
    admin = create_user("estop_admin_resume", UserRole.ADMIN)
    cookies, headers = _auth_headers_and_cookies(web_session, admin)

    with patch.object(EmergencyStop, "resume") as mock_resume:
        resp = client.post(
            "/api/v1/system/emergency-resume",
            json={"reason": "Root cause mitigated"},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_active"] is False
        assert data["status"] == "INACTIVE (OPERATIONAL)"
        mock_resume.assert_called_once_with(reason="Root cause mitigated")


def test_runner_start_campaign_not_found(client, web_session, create_user):
    operator = create_user("op_not_found", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(
        RunnerControlService,
        "start_runner",
        return_value=(False, "Target Campaign 9999 was not found.", None)
    ):
        resp = client.post(
            "/api/v1/runner/start",
            json={"campaign_id": 9999},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]["message"]


def test_runner_start_generic_bad_request(client, web_session, create_user):
    operator = create_user("op_bad_req", UserRole.OPERATOR)
    cookies, headers = _auth_headers_and_cookies(web_session, operator)

    with patch.object(
        RunnerControlService,
        "start_runner",
        return_value=(False, "Preflight readiness check failed: DB down", None)
    ):
        resp = client.post(
            "/api/v1/runner/start",
            json={"campaign_id": 1},
            cookies=cookies,
            headers=headers
        )
        assert resp.status_code == 400
        assert "Preflight" in resp.json()["error"]["message"]
