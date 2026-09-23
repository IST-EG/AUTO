"""
Tests for Phase 7.7-B Step 7 — PREFLIGHT Command Execution & API Endpoint.

Verifies:
- PREFLIGHT RBAC authorization: VIEWER gets 403, OPERATOR/ADMIN/OWNER get 200
- PREFLIGHT CSRF protection: missing CSRF gets 403
- PREFLIGHT 120-second command lease (vs 60-second default for standard commands)
- PREFLIGHT does not cold-start Chrome
- PREFLIGHT does not start WhatsApp Web
- PREFLIGHT does not send messages
- PREFLIGHT inspection behavior when provider is already active
- PREFLIGHT inspection behavior when provider is None
- PREFLIGHT single in-flight command serialization (HTTP 409 Conflict)
- PREFLIGHT persistence into system:last_preflight_result
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.models.user import UserRole
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.services.whatsapp_command_service import WhatsAppCommandService
from app.runner.whatsapp_command_handler import WhatsAppCommandHandler


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


# ==============================================================================
# 1. API Route & RBAC / CSRF
# ==============================================================================

class TestPreflightAPIRoute:
    """Tests for POST /api/v1/whatsapp/preflight endpoint."""

    def test_preflight_unauthenticated_returns_401(self, client):
        """Unauthenticated request with valid CSRF must return 401 Unauthorized."""
        csrf_token = csrf_manager.generate_token()
        cookies = {web_settings.WEB_CSRF_COOKIE_NAME: csrf_token}
        headers = {"X-CSRF-Token": csrf_token}
        res = client.post("/api/v1/whatsapp/preflight", cookies=cookies, headers=headers)
        assert res.status_code == 401


    def test_preflight_viewer_returns_403(self, client, web_session, create_user):
        """VIEWER role must receive 403 Forbidden."""
        viewer = create_user(username="viewer_preflight", role=UserRole.VIEWER)
        cookies, headers = _auth(web_session, viewer)

        res = client.post("/api/v1/whatsapp/preflight", cookies=cookies, headers=headers)
        assert res.status_code == 403

    def test_preflight_operator_missing_csrf_returns_403(self, client, web_session, create_user):
        """OPERATOR without CSRF header must receive 403 Forbidden."""
        operator = create_user(username="op_preflight_nocsrf", role=UserRole.OPERATOR)
        cookies, _ = _auth(web_session, operator)

        res = client.post("/api/v1/whatsapp/preflight", cookies=cookies)
        assert res.status_code == 403

    def test_preflight_operator_success(self, client, web_session, create_user):
        """OPERATOR with valid CSRF must receive 200 OK and REQUESTED command."""
        operator = create_user(username="op_preflight", role=UserRole.OPERATOR)
        cookies, headers = _auth(web_session, operator)

        res = client.post(
            "/api/v1/whatsapp/preflight",
            json={"reason": "Nightly readiness check"},
            cookies=cookies,
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()["data"]

        assert data["action"] == "PREFLIGHT"
        assert data["status"] == "REQUESTED"
        assert data["requested_by"] == "op_preflight"
        assert data["request_id"].startswith("req_wa_")
        assert data["params"]["reason"] == "Nightly readiness check"

        # Verify audit log was emitted
        audit = (
            web_session.query(AuditLog)
            .filter(AuditLog.event_type == "WHATSAPP_PREFLIGHT_REQUESTED")
            .first()
        )
        assert audit is not None
        assert audit.status == "REQUESTED"

    def test_preflight_concurrent_conflict_returns_409(self, client, web_session, create_user):
        """Submitting PREFLIGHT while another command is active returns HTTP 409 Conflict."""
        operator = create_user(username="op_preflight_conflict", role=UserRole.OPERATOR)
        cookies, headers = _auth(web_session, operator)

        # 1. First command
        res1 = client.post("/api/v1/whatsapp/preflight", cookies=cookies, headers=headers)
        assert res1.status_code == 200

        # 2. Second command immediately -> 409
        res2 = client.post("/api/v1/whatsapp/preflight", cookies=cookies, headers=headers)
        assert res2.status_code == 409
        assert "currently in progress" in res2.json()["error"]["message"]


# ==============================================================================
# 2. Worker-side Lease Duration (120 seconds)
# ==============================================================================

class TestPreflightLeaseDuration:
    """Verifies that PREFLIGHT receives a 120-second lease while others receive 60s."""

    def test_preflight_receives_120s_lease(self, web_session):
        """When worker claims PREFLIGHT, lease_expires_at must be ~120s in future."""
        WhatsAppCommandService.submit_command(
            db=web_session,
            action="PREFLIGHT",
            requested_by="operator1",
        )

        handler = WhatsAppCommandHandler(db=web_session, worker_id="worker_test_120")

        # Mock out preflight execution to only test claiming behavior
        with patch.object(handler, "_execute_preflight") as mock_exec:
            claimed = handler.poll_and_execute()

        assert claimed is not None
        assert claimed["action"] == "PREFLIGHT"
        assert claimed["lease_expires_at"] is not None

        lease_dt = datetime.fromisoformat(claimed["lease_expires_at"].replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        diff = (lease_dt - now_utc).total_seconds()
        # Expect ~120s (allow 115-125s tolerance)
        assert 115 <= diff <= 125, f"Expected ~120s lease, got {diff:.1f}s"

    def test_standard_command_receives_60s_lease(self, web_session):
        """Standard commands (e.g. HEALTH_CHECK) must still receive 60s lease."""
        WhatsAppCommandService.submit_command(
            db=web_session,
            action="HEALTH_CHECK",
            requested_by="operator1",
        )

        handler = WhatsAppCommandHandler(db=web_session, worker_id="worker_test_60")

        with patch.object(handler, "_execute_health_check"):
            claimed = handler.poll_and_execute()

        assert claimed is not None
        lease_dt = datetime.fromisoformat(claimed["lease_expires_at"].replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        diff = (lease_dt - now_utc).total_seconds()
        assert 55 <= diff <= 65, f"Expected ~60s lease, got {diff:.1f}s"


# ==============================================================================
# 3. PREFLIGHT Safety Invariants (No Chrome, No WhatsApp, No Messages)
# ==============================================================================

class TestPreflightSafetyInvariants:
    """Verifies strict safety requirements: no Chrome cold start, no WhatsApp launch, no messages."""

    def test_preflight_no_chrome_cold_start_when_provider_is_none(self, web_session):
        """PREFLIGHT execution with provider=None must not launch Chrome subprocess or browser."""
        WhatsAppCommandService.submit_command(
            db=web_session,
            action="PREFLIGHT",
            requested_by="safety_tester",
        )

        handler = WhatsAppCommandHandler(db=web_session, worker_id="safety_worker", provider=None)

        # Mock out subprocess.Popen and Selenium WebDriver to ensure neither is called
        with patch("subprocess.Popen") as mock_popen, \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_xvfb", return_value=True), \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_chrome", return_value=True), \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_chromedriver", return_value=True), \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_session_profile", return_value=True):

            claimed = handler.poll_and_execute()

        assert claimed is not None
        assert mock_popen.call_count == 0

        # Verify command outcome completed
        last_cmd = WhatsAppCommandService.get_last_completed_command(web_session)
        assert last_cmd["status"] == "COMPLETED"
        assert "PREFLIGHT" in last_cmd["result"]

        # Verify persisted preflight result in app_settings
        setting = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.PREFLIGHT_RESULT_KEY
        ).first()
        assert setting is not None
        result_data = json.loads(setting.value)

        # Verify explicit safety invariant flags in payload
        assert result_data["chrome_cold_started"] is False
        assert result_data["whatsapp_launched"] is False
        assert result_data["messages_sent"] == 0
        assert result_data["infra_checks"]["provider_active"] is False

    def test_preflight_when_provider_already_active(self, web_session):
        """When provider is already active, PREFLIGHT queries provider readiness without launching new one."""
        WhatsAppCommandService.submit_command(
            db=web_session,
            action="PREFLIGHT",
            requested_by="safety_tester_active",
        )

        mock_provider = MagicMock()
        mock_provider.health_check.return_value = True

        handler = WhatsAppCommandHandler(
            db=web_session,
            worker_id="active_provider_worker",
            provider=mock_provider,
        )

        with patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_xvfb", return_value=True), \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_chrome", return_value=True), \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_chromedriver", return_value=True), \
             patch("app.runner.whatsapp_command_handler.WhatsAppCommandHandler._check_session_profile", return_value=True):

            claimed = handler.poll_and_execute()

        assert claimed is not None

        # Verify provider.connect() or send_message was NEVER called
        assert getattr(mock_provider, "connect", MagicMock()).call_count == 0
        assert getattr(mock_provider, "send_message", MagicMock()).call_count == 0

        # Verify completed result
        last_cmd = WhatsAppCommandService.get_last_completed_command(web_session)
        assert last_cmd["status"] == "COMPLETED"

        setting = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.PREFLIGHT_RESULT_KEY
        ).first()
        result_data = json.loads(setting.value)
        assert result_data["infra_checks"]["provider_active"] is True
        assert result_data["messages_sent"] == 0

    def test_preflight_handles_exception_gracefully(self, web_session):
        """If run_preflight raises an unexpected exception, command fails cleanly without crash."""
        WhatsAppCommandService.submit_command(
            db=web_session,
            action="PREFLIGHT",
            requested_by="error_tester",
        )

        handler = WhatsAppCommandHandler(db=web_session, worker_id="error_worker")

        with patch("app.readiness.preflight.run_preflight", side_effect=RuntimeError("Simulated DB connection failure")):
            claimed = handler.poll_and_execute()

        assert claimed is not None
        last_cmd = WhatsAppCommandService.get_last_completed_command(web_session)
        assert last_cmd is not None
        assert last_cmd["status"] == "COMPLETED"
        assert "PARTIAL" in last_cmd["result"]
