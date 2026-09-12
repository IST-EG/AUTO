"""
Unit and integration tests for Vercel Control Plane configuration and security.

Verifies:
1. Pydantic configuration aliases (APP_ENV <-> ENVIRONMENT, COOKIE_SECURE <-> WEB_COOKIE_SECURE, SECRET_KEY <-> WEB_SECRET_KEY).
2. DEBUG and VERCEL flags across web_settings and app settings.
3. Cookie Secure attribute behavior under COOKIE_SECURE=True vs COOKIE_SECURE=False.
4. TrustedHostMiddleware host header enforcement (blocking unauthorized domains, allowing auto.integra-ist.com and *.vercel.app).
5. Strict Vercel boundary enforcement (zero browser/runner imports in app/web, and zero OS process spawning under VERCEL=1).
"""

import os
import ast
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from starlette.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.web.config import WebSettings, web_settings
from app.utils.settings import Settings
from app.web.app import create_app
from app.models.campaign import Campaign
from app.web.services.runner_control_service import RunnerControlService


# ==============================================================================
# 1. Configuration Alias and Normalization Tests
# ==============================================================================

def test_web_settings_app_env_sync():
    """APP_ENV and ENVIRONMENT must synchronize bidirectionally."""
    # When APP_ENV is passed
    s1 = WebSettings(APP_ENV="production")
    assert s1.ENVIRONMENT == "production"
    assert s1.APP_ENV == "production"

    # When ENVIRONMENT is passed
    s2 = WebSettings(ENVIRONMENT="staging")
    assert s2.APP_ENV == "staging"
    assert s2.ENVIRONMENT == "staging"


def test_web_settings_cookie_secure_sync():
    """COOKIE_SECURE and WEB_COOKIE_SECURE must synchronize bidirectionally."""
    # Explicit COOKIE_SECURE=True
    s1 = WebSettings(COOKIE_SECURE=True)
    assert s1.WEB_COOKIE_SECURE is True
    assert s1.COOKIE_SECURE is True

    # Explicit COOKIE_SECURE=False in development
    s2 = WebSettings(ENVIRONMENT="development", COOKIE_SECURE=False)
    assert s2.WEB_COOKIE_SECURE is False
    assert s2.COOKIE_SECURE is False

    # Production environment automatically defaults COOKIE_SECURE to True
    s3 = WebSettings(APP_ENV="production")
    assert s3.WEB_COOKIE_SECURE is True
    assert s3.COOKIE_SECURE is True


def test_web_settings_secret_key_sync():
    """SECRET_KEY and WEB_SECRET_KEY must synchronize bidirectionally."""
    test_key = "custom_secret_key_for_testing_1234567890_abcdef"
    
    # Passing SECRET_KEY populates WEB_SECRET_KEY
    s1 = WebSettings(SECRET_KEY=test_key)
    assert s1.WEB_SECRET_KEY == test_key
    assert s1.SECRET_KEY == test_key

    # Passing WEB_SECRET_KEY populates SECRET_KEY
    s2 = WebSettings(WEB_SECRET_KEY=test_key)
    assert s2.SECRET_KEY == test_key
    assert s2.WEB_SECRET_KEY == test_key


def test_web_settings_debug_and_vercel_flags():
    """DEBUG and VERCEL flags must be supported."""
    ws = WebSettings(DEBUG=True, VERCEL="1")
    assert ws.DEBUG is True
    assert ws.VERCEL == "1"

    app_settings = Settings(APP_ENV="production", DEBUG=False, VERCEL="1")
    assert app_settings.APP_ENV == "production"
    assert app_settings.DEBUG is False
    assert app_settings.VERCEL == "1"


# ==============================================================================
# 2. Cookie Security Enforcement Tests
# ==============================================================================

def test_cookie_secure_attribute_when_enabled(client, create_user):
    """When WEB_COOKIE_SECURE=True, CSRF and Session cookies must have Secure flag."""
    original_secure = web_settings.WEB_COOKIE_SECURE
    try:
        web_settings.WEB_COOKIE_SECURE = True

        # CSRF endpoint
        csrf_resp = client.get("/api/v1/auth/csrf")
        assert csrf_resp.status_code == 200
        set_cookie_header = csrf_resp.headers.get("set-cookie", "").lower()
        assert "secure" in set_cookie_header

        # Login endpoint
        create_user(username="secure_cookie_user", password="Password123!#")
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "secure_cookie_user",
            "password": "Password123!#"
        })
        assert login_resp.status_code == 200
        login_cookies = login_resp.headers.get_list("set-cookie") if hasattr(login_resp.headers, "get_list") else [login_resp.headers.get("set-cookie", "")]
        for cookie_str in login_cookies:
            assert "secure" in cookie_str.lower()
    finally:
        web_settings.WEB_COOKIE_SECURE = original_secure


def test_cookie_secure_attribute_when_disabled(client):
    """When WEB_COOKIE_SECURE=False (local dev), cookies should not require Secure flag."""
    original_secure = web_settings.WEB_COOKIE_SECURE
    try:
        web_settings.WEB_COOKIE_SECURE = False

        csrf_resp = client.get("/api/v1/auth/csrf")
        assert csrf_resp.status_code == 200
        set_cookie_header = csrf_resp.headers.get("set-cookie", "").lower()
        # Should not have secure flag
        assert "secure" not in set_cookie_header
    finally:
        web_settings.WEB_COOKIE_SECURE = original_secure


# ==============================================================================
# 3. TrustedHostMiddleware Enforcement Tests
# ==============================================================================

def test_trusted_host_middleware_with_restricted_hosts(monkeypatch):
    """Restricted ALLOWED_HOSTS must allow matching hosts and reject others with HTTP 400."""
    monkeypatch.setattr(web_settings, "ALLOWED_HOSTS", "auto.integra-ist.com,*.vercel.app")
    app_with_hosts = create_app()

    # Client configured to allow auto.integra-ist.com
    c1 = TestClient(app_with_hosts, base_url="https://auto.integra-ist.com")
    resp1 = c1.get("/health")
    assert resp1.status_code == 200

    # Client configured to allow subdomain of vercel.app
    c2 = TestClient(app_with_hosts, base_url="https://preview-deploy.vercel.app")
    resp2 = c2.get("/health")
    assert resp2.status_code == 200

    # Client configured with disallowed host
    c3 = TestClient(app_with_hosts, base_url="https://malicious-domain.com")
    resp3 = c3.get("/health")
    assert resp3.status_code == 400
    assert "Invalid host header" in resp3.text


def test_trusted_host_middleware_wildcard_allows_all(monkeypatch):
    """Default ALLOWED_HOSTS='*' allows any host header without rejection."""
    monkeypatch.setattr(web_settings, "ALLOWED_HOSTS", "*")
    app_wildcard = create_app()

    c = TestClient(app_wildcard, base_url="https://any-arbitrary-domain.org")
    resp = c.get("/health")
    assert resp.status_code == 200


# ==============================================================================
# 4. Strict Vercel Execution Boundary Audit
# ==============================================================================

def test_web_layer_no_browser_or_runner_imports():
    """Verify that app/web modules never import selenium, webdriver, or ProductionRunner."""
    web_root = Path(__file__).resolve().parent.parent.parent / "app" / "web"
    forbidden_modules = {"selenium", "chromedriver", "webdriver"}

    for py_file in web_root.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        parsed = ast.parse(content, filename=str(py_file))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_modules:
                        assert forbidden not in alias.name.lower(), (
                            f"Forbidden module import '{alias.name}' found in {py_file}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in forbidden_modules:
                        assert forbidden not in node.module.lower(), (
                            f"Forbidden from-import '{node.module}' found in {py_file}"
                        )
                for alias in node.names:
                    assert alias.name not in ("ProductionRunner", "QueueWorker"), (
                        f"Forbidden symbol import '{alias.name}' found in {py_file}"
                    )


def test_runner_control_service_vercel_mode_avoids_os_execution(web_session):
    """Verify that under VERCEL=1, RunnerControlService never invokes subprocess.Popen or os.kill."""
    camp = Campaign(
        name="Vercel Boundary Test Campaign",
        message_template="Hello {name}",
        status="RUNNING",
    )
    web_session.add(camp)
    web_session.commit()

    mock_preflight = MagicMock(passed=True, checks=[])

    with patch.dict(os.environ, {"VERCEL": "1"}), \
         patch("app.web.services.runner_control_service.run_preflight", return_value=mock_preflight), \
         patch("app.web.services.runner_control_service.subprocess.Popen") as mock_popen, \
         patch("os.kill") as mock_kill:

        # 1. Start runner: coordinates via DB, never spawns OS subprocess
        success, msg, data = RunnerControlService.start_runner(
            db=web_session,
            campaign_id=camp.id,
            operator_username="operator_vercel"
        )
        assert success is True
        assert data["desired_state"] == "RUNNING"
        assert data["pid"] is None
        mock_popen.assert_not_called()

        # 2. Stop runner: coordinates via DB, never sends OS kill signals
        stop_success, stop_msg, stop_data = RunnerControlService.stop_runner(
            db=web_session,
            operator_username="operator_vercel"
        )
        assert stop_success is True
        assert stop_data["desired_state"] == "STOPPED"
        mock_kill.assert_not_called()
