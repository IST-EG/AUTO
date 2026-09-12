"""
Tests for Double-Submit CSRF Protection.

Verifies:
- Valid signed CSRF token allows state-changing requests
- Missing header is rejected with 403
- Missing cookie is rejected with 403
- Tampered/invalid signature is rejected with 403
- Mismatched token values are rejected with 403
- GET requests do not require CSRF token
"""

import pytest
from fastapi import APIRouter, Depends
from app.web.dependencies import verify_csrf
from app.web.config import web_settings
from app.web.security.csrf import csrf_manager

csrf_test_router = APIRouter(prefix="/api/v1/test-csrf")

@csrf_test_router.post("/action", dependencies=[Depends(verify_csrf)])
def mutate_action():
    return {"status": "action_performed"}

@csrf_test_router.get("/safe-action", dependencies=[Depends(verify_csrf)])
def safe_action():
    return {"status": "safe_read"}


@pytest.fixture(autouse=True)
def mount_csrf_router(client):
    from app.web.app import app
    app.include_router(csrf_test_router)
    yield


def test_csrf_get_safe_request_succeeds_without_tokens(client):
    resp = client.get("/api/v1/test-csrf/safe-action")
    assert resp.status_code == 200
    assert resp.json()["status"] == "safe_read"


def test_csrf_valid_token_succeeds(client):
    valid_token = csrf_manager.generate_token()

    resp = client.post(
        "/api/v1/test-csrf/action",
        cookies={web_settings.WEB_CSRF_COOKIE_NAME: valid_token},
        headers={"X-CSRF-Token": valid_token}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "action_performed"


def test_csrf_missing_header_rejected(client):
    valid_token = csrf_manager.generate_token()

    resp = client.post(
        "/api/v1/test-csrf/action",
        cookies={web_settings.WEB_CSRF_COOKIE_NAME: valid_token}
        # Missing X-CSRF-Token header
    )
    assert resp.status_code == 403
    assert "Missing X-CSRF-Token header" in resp.json()["error"]["message"]


def test_csrf_missing_cookie_rejected(client):
    valid_token = csrf_manager.generate_token()

    resp = client.post(
        "/api/v1/test-csrf/action",
        # Missing cookie
        headers={"X-CSRF-Token": valid_token}
    )
    assert resp.status_code == 403
    assert "Missing CSRF cookie" in resp.json()["error"]["message"]


def test_csrf_tampered_signature_rejected(client):
    valid_token = csrf_manager.generate_token()
    raw_val = valid_token.split(".")[0]
    tampered_token = f"{raw_val}.forged_signature_12345678"

    resp = client.post(
        "/api/v1/test-csrf/action",
        cookies={web_settings.WEB_CSRF_COOKIE_NAME: tampered_token},
        headers={"X-CSRF-Token": tampered_token}
    )
    assert resp.status_code == 403
    assert "Invalid CSRF" in resp.json()["error"]["message"]


def test_csrf_mismatched_tokens_rejected(client):
    token_a = csrf_manager.generate_token()
    token_b = csrf_manager.generate_token()

    resp = client.post(
        "/api/v1/test-csrf/action",
        cookies={web_settings.WEB_CSRF_COOKIE_NAME: token_a},
        headers={"X-CSRF-Token": token_b}
    )
    assert resp.status_code == 403
    assert "mismatch" in resp.json()["error"]["message"].lower()
