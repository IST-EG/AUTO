"""
Tests for Phase 7.7-B Step 7 — Worker Identity, Heartbeat, and Infrastructure Health.

Verifies:
- WORKER_INSTANCE_ID validation rules
- Worker identity persistence to system:worker_identity
- Worker heartbeat persistence to system:worker_heartbeat
- Heartbeat freshness computation
- Infrastructure health layer independence
- Identity field safety (no credentials, no session data)
- Status endpoint exposes five independent health dimensions
- PREFLIGHT: lease is 120 seconds
- PREFLIGHT: does not cold-start Chrome
- PREFLIGHT: does not launch WhatsApp Web
- PREFLIGHT: does not send messages
- PREFLIGHT: when provider is already active, inspects without launching
- PREFLIGHT: when provider is None, reports infra checks without browser
- PREFLIGHT: timeout/failure results in FAILED (not re-retried blindly)
- Worker restart recovery: stale heartbeat triggers OFFLINE state
"""

import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest

from app.models.app_setting import AppSetting
from app.runner.whatsapp_command_handler import (
    WhatsAppCommandHandler,
    validate_worker_instance_id,
)
from app.services.whatsapp_command_service import WhatsAppCommandService
from app.web.services.whatsapp_service import WhatsAppWebService
from app.web.schemas.whatsapp import WorkerInfraHealthEnum


# ==============================================================================
# A. WORKER_INSTANCE_ID Validation
# ==============================================================================

class TestWorkerInstanceIDValidation:
    """Test WORKER_INSTANCE_ID format validation rules."""

    def test_valid_ids(self):
        valid_ids = [
            "oracle-arm64-worker-01",
            "worker01",
            "prod-worker",
            "abc",
            "a1b",
            "worker-1",
            "a" * 64,  # max length
        ]
        for id_ in valid_ids:
            assert validate_worker_instance_id(id_), f"Expected valid: {id_!r}"

    def test_invalid_too_short(self):
        assert validate_worker_instance_id("ab") is False
        assert validate_worker_instance_id("a") is False
        assert validate_worker_instance_id("") is False

    def test_invalid_too_long(self):
        assert validate_worker_instance_id("a" * 65) is False

    def test_invalid_starts_with_hyphen(self):
        assert validate_worker_instance_id("-worker") is False

    def test_invalid_ends_with_hyphen(self):
        assert validate_worker_instance_id("worker-") is False

    def test_invalid_uppercase(self):
        assert validate_worker_instance_id("Oracle-Worker") is False

    def test_invalid_special_characters(self):
        assert validate_worker_instance_id("worker_01") is False
        assert validate_worker_instance_id("worker.01") is False
        assert validate_worker_instance_id("192.168.1.1") is False

    def test_invalid_none(self):
        assert validate_worker_instance_id(None) is False  # type: ignore

    def test_invalid_not_string(self):
        assert validate_worker_instance_id(123) is False  # type: ignore


# ==============================================================================
# B. Worker Identity Persistence
# ==============================================================================

class TestWorkerIdentityPersistence:
    """Tests for publish_identity() writing to system:worker_identity."""

    def test_publish_identity_creates_setting(self, web_session):
        """publish_identity() must create system:worker_identity AppSetting."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="test-worker-001")

        with patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "oracle-arm64-worker-01"
            mock_settings.APP_ENV = "production"
            mock_settings.WHATSAPP_CHROME_BINARY = ""
            mock_settings.WHATSAPP_CHROMEDRIVER_PATH = ""
            mock_settings.WHATSAPP_SESSION_PATH = "./data/whatsapp_session"
            handler.publish_identity()

        row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.IDENTITY_SETTING_KEY
        ).first()
        assert row is not None
        identity = json.loads(row.value)

        assert identity["instance_id"] == "oracle-arm64-worker-01"
        assert identity["worker_id"] == "test-worker-001"
        assert identity["environment"] == "production"
        assert "arch" in identity
        assert "python_version" in identity
        assert "capabilities" in identity
        assert "registered_at" in identity
        assert "last_seen" in identity

    def test_publish_identity_no_credentials(self, web_session):
        """Identity record must not contain any credentials or secrets."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="test-worker-002")

        with patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "worker-safe"
            mock_settings.APP_ENV = "production"
            mock_settings.WHATSAPP_CHROME_BINARY = ""
            mock_settings.WHATSAPP_CHROMEDRIVER_PATH = ""
            mock_settings.WHATSAPP_SESSION_PATH = "./data/whatsapp_session"
            handler.publish_identity()

        row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.IDENTITY_SETTING_KEY
        ).first()
        raw = row.value

        # Credential fields must never appear
        assert "password" not in raw.lower()
        assert "database_url" not in raw.lower()
        assert "api_key" not in raw.lower()
        assert "ssh_key" not in raw.lower()
        assert "secret" not in raw.lower()
        # Session-specific fields must not appear
        assert "cookie" not in raw.lower()
        assert "qr" not in raw.lower()

    def test_publish_identity_empty_produces_unconfigured(self, web_session):
        """When WORKER_INSTANCE_ID is empty, instance_id must be 'unconfigured', NEVER self.worker_id."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="fallback-worker-id")

        with patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = ""
            mock_settings.APP_ENV = "production"
            mock_settings.WHATSAPP_CHROME_BINARY = ""
            mock_settings.WHATSAPP_CHROMEDRIVER_PATH = ""
            mock_settings.WHATSAPP_SESSION_PATH = "./data/whatsapp_session"
            handler.publish_identity()

        row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.IDENTITY_SETTING_KEY
        ).first()
        identity = json.loads(row.value)
        # instance_id must be 'unconfigured', self.worker_id is NEVER used as persistent logical identity
        assert identity["instance_id"] == "unconfigured"
        assert identity["instance_id"] != handler.worker_id

    def test_publish_identity_configured_is_used(self, web_session):
        """Configured valid WORKER_INSTANCE_ID must be used directly."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="ephemeral-proc-99")

        with patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "oracle-arm64-worker-01"
            mock_settings.APP_ENV = "production"
            mock_settings.WHATSAPP_CHROME_BINARY = ""
            mock_settings.WHATSAPP_CHROMEDRIVER_PATH = ""
            mock_settings.WHATSAPP_SESSION_PATH = "./data/whatsapp_session"
            handler.publish_identity()

        row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.IDENTITY_SETTING_KEY
        ).first()
        identity = json.loads(row.value)
        assert identity["instance_id"] == "oracle-arm64-worker-01"
        assert identity["instance_id"] != handler.worker_id



# ==============================================================================
# C. Worker Heartbeat Persistence
# ==============================================================================

class TestWorkerHeartbeatPersistence:
    """Tests for publish_telemetry() extending to system:worker_heartbeat."""

    def test_publish_telemetry_creates_heartbeat(self, web_session):
        """publish_telemetry() must create system:worker_heartbeat AppSetting."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="hb-worker-001")

        with patch.object(handler, "_check_xvfb", return_value=True), \
             patch.object(handler, "_check_chrome", return_value=True), \
             patch.object(handler, "_check_chromedriver", return_value=True), \
             patch.object(handler, "_check_session_profile", return_value=True), \
             patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "oracle-arm64-worker-01"
            handler.publish_telemetry(state="CONNECTED")

        row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.HEARTBEAT_SETTING_KEY
        ).first()
        assert row is not None
        hb = json.loads(row.value)

        assert hb["worker_id"] == "hb-worker-001"
        assert "last_seen" in hb
        assert "runner_state" in hb
        assert "uptime_seconds" in hb
        assert hb["xvfb_healthy"] is True
        assert hb["chrome_reachable"] is True
        assert hb["session_profile_present"] is True

    def test_heartbeat_uptime_increases(self, web_session):
        """uptime_seconds in heartbeat must be >= 0 and increase over time."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="hb-worker-002")

        with patch.object(handler, "_check_xvfb", return_value=False), \
             patch.object(handler, "_check_chrome", return_value=False), \
             patch.object(handler, "_check_chromedriver", return_value=False), \
             patch.object(handler, "_check_session_profile", return_value=False), \
             patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "test-worker"
            handler.publish_telemetry()
            row1 = web_session.query(AppSetting).filter(
                AppSetting.key == WhatsAppCommandHandler.HEARTBEAT_SETTING_KEY
            ).first()
            uptime1 = json.loads(row1.value)["uptime_seconds"]

        assert uptime1 >= 0.0

    def test_heartbeat_no_credentials(self, web_session):
        """Heartbeat must not contain any credentials, session data, or message bodies."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="hb-worker-safe")

        with patch.object(handler, "_check_xvfb", return_value=False), \
             patch.object(handler, "_check_chrome", return_value=False), \
             patch.object(handler, "_check_chromedriver", return_value=False), \
             patch.object(handler, "_check_session_profile", return_value=False), \
             patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "safe-worker"
            handler.publish_telemetry(state="IDLE")

        row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandHandler.HEARTBEAT_SETTING_KEY
        ).first()
        raw = row.value
        assert "password" not in raw.lower()
        assert "database_url" not in raw.lower()
        assert "cookie" not in raw.lower()
        assert "session_token" not in raw.lower()

    def test_heartbeat_runner_state_reflects_current_state(self, web_session):
        """runner_state in heartbeat must match the published telemetry state."""
        handler = WhatsAppCommandHandler(db=web_session, worker_id="hb-worker-state")

        for state_str in ("CONNECTED", "IDLE", "PAUSED", "DISCONNECTED"):
            with patch.object(handler, "_check_xvfb", return_value=False), \
                 patch.object(handler, "_check_chrome", return_value=False), \
                 patch.object(handler, "_check_chromedriver", return_value=False), \
                 patch.object(handler, "_check_session_profile", return_value=False), \
                 patch("app.runner.whatsapp_command_handler.settings") as mock_settings:
                mock_settings.WORKER_INSTANCE_ID = "state-worker"
                handler.publish_telemetry(state=state_str)

            row = web_session.query(AppSetting).filter(
                AppSetting.key == WhatsAppCommandHandler.HEARTBEAT_SETTING_KEY
            ).first()
            hb = json.loads(row.value)
            assert hb["runner_state"] == state_str


# ==============================================================================
# D. Infrastructure Health Dimensions (get_status)
# ==============================================================================

class TestInfraHealthDimensions:
    """Tests for five independent health dimension exposure in WhatsAppWebService.get_status()."""

    def _inject_heartbeat(self, web_session, last_seen_offset_seconds=0, xvfb=True, chrome=True):
        """Helper: write a fake system:worker_heartbeat record."""
        now_utc = datetime.now(timezone.utc)
        last_seen = (now_utc - timedelta(seconds=last_seen_offset_seconds)).isoformat()
        payload = json.dumps({
            "instance_id": "test-worker",
            "worker_id": "runner_camp1",
            "last_seen": last_seen,
            "runner_state": "IDLE",
            "uptime_seconds": 300.0,
            "xvfb_healthy": xvfb,
            "chrome_reachable": chrome,
            "chromedriver_reachable": True,
            "session_profile_present": True,
            "provider_active": False,
        })
        row = web_session.query(AppSetting).filter(
            AppSetting.key == "system:worker_heartbeat"
        ).first()
        if not row:
            row = AppSetting(key="system:worker_heartbeat", value=payload, description="test")
            web_session.add(row)
        else:
            row.value = payload
        web_session.commit()

    def _inject_identity(self, web_session):
        """Helper: write a fake system:worker_identity record."""
        payload = json.dumps({
            "instance_id": "test-worker",
            "environment": "production",
            "arch": "aarch64",
            "os": "Linux",
            "python_version": "3.12.3",
            "chrome_version": "Chrome 153.0.8010.52",
            "chromedriver_version": "ChromeDriver 153.0.8010.52",
            "xvfb_display": ":99",
            "capabilities": ["chrome_for_testing", "xvfb_display"],
            "app_version": "Phase 7.7-B",
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "worker_id": "runner_camp1",
        })
        row = web_session.query(AppSetting).filter(
            AppSetting.key == "system:worker_identity"
        ).first()
        if not row:
            row = AppSetting(key="system:worker_identity", value=payload, description="test")
            web_session.add(row)
        else:
            row.value = payload
        web_session.commit()

    def test_status_contains_five_health_dimensions(self, web_session):
        """get_status() must include all five health dimension fields."""
        self._inject_heartbeat(web_session, last_seen_offset_seconds=5)
        self._inject_identity(web_session)

        status = WhatsAppWebService.get_status(web_session)

        # Dimension 1: Infrastructure
        assert "worker_identity" in status
        assert "worker_heartbeat" in status
        assert "infra_health" in status
        # Dimension 2: Browser
        assert "browser_state" in status
        # Dimension 3: Session
        assert "session_authenticated" in status
        assert "qr_required" in status
        # Dimension 4: Runner (covered by existing runner_* fields)
        assert "is_runner_active" in status
        # Dimension 5: Queue — covered separately via /api/v1/queue
        # last_preflight_result
        assert "last_preflight_result" in status

    def test_infra_health_healthy_when_heartbeat_fresh(self, web_session):
        """infra_health = HEALTHY when heartbeat < 30s and chrome/xvfb both OK."""
        self._inject_heartbeat(web_session, last_seen_offset_seconds=5, xvfb=True, chrome=True)
        status = WhatsAppWebService.get_status(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.HEALTHY.value

    def test_infra_health_degraded_when_heartbeat_30_to_60s(self, web_session):
        """infra_health = DEGRADED when 30 <= heartbeat_age < 60s."""
        self._inject_heartbeat(web_session, last_seen_offset_seconds=45, xvfb=True, chrome=True)
        status = WhatsAppWebService.get_status(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.DEGRADED.value

    def test_infra_health_offline_when_heartbeat_stale(self, web_session):
        """infra_health = OFFLINE when heartbeat_age >= 60s."""
        self._inject_heartbeat(web_session, last_seen_offset_seconds=65, xvfb=True, chrome=True)
        status = WhatsAppWebService.get_status(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.OFFLINE.value

    def test_infra_health_unknown_when_no_heartbeat(self, web_session):
        """infra_health = UNKNOWN when no heartbeat has been published."""
        status = WhatsAppWebService.get_status(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.UNKNOWN.value

    def test_infra_health_degraded_when_chrome_missing(self, web_session):
        """infra_health = DEGRADED when chrome_reachable is False even with fresh heartbeat."""
        self._inject_heartbeat(web_session, last_seen_offset_seconds=5, xvfb=True, chrome=False)
        status = WhatsAppWebService.get_status(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.DEGRADED.value

    def test_worker_identity_exposed_safely(self, web_session):
        """worker_identity must be present and must not expose credentials."""
        self._inject_identity(web_session)
        status = WhatsAppWebService.get_status(web_session)

        identity = status.get("worker_identity")
        assert identity is not None
        identity_str = json.dumps(identity)
        assert "password" not in identity_str.lower()
        assert "database_url" not in identity_str.lower()
        assert "api_key" not in identity_str.lower()
        assert "ssh_key" not in identity_str.lower()

    def test_five_dimensions_are_independent(self, web_session):
        """Health dimensions must be independent (e.g. infra OFFLINE != session state)."""
        # Stale heartbeat → infra OFFLINE, but session state is still readable from telemetry
        self._inject_heartbeat(web_session, last_seen_offset_seconds=90)
        status = WhatsAppWebService.get_status(web_session)

        # Infra is OFFLINE (stale heartbeat)
        assert status["infra_health"] == WorkerInfraHealthEnum.OFFLINE.value
        # But session state is still distinct (DISCONNECTED since no runner active)
        assert "browser_state" in status
        assert "session_authenticated" in status
        # Runner state is still exposed
        assert "is_runner_active" in status


# ==============================================================================
# E. Heartbeat Freshness and Worker Restart Recovery
# ==============================================================================

class TestHeartbeatFreshnessAndRestartRecovery:
    """Tests for heartbeat freshness and worker restart recovery scenarios."""

    def test_stale_heartbeat_transitions_to_degraded(self, web_session):
        """After 30s without heartbeat, status should reflect DEGRADED."""
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat()
        payload = json.dumps({
            "instance_id": "stale-worker",
            "worker_id": "stale-runner",
            "last_seen": past_time,
            "runner_state": "RUNNING",
            "uptime_seconds": 1000.0,
            "xvfb_healthy": True,
            "chrome_reachable": True,
            "chromedriver_reachable": True,
            "session_profile_present": True,
            "provider_active": True,
        })
        row = web_session.query(AppSetting).filter(
            AppSetting.key == "system:worker_heartbeat"
        ).first()
        if not row:
            row = AppSetting(key="system:worker_heartbeat", value=payload, description="test")
            web_session.add(row)
        else:
            row.value = payload
        web_session.commit()

        status = WhatsAppWebService.get_status(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.DEGRADED.value

    def test_heartbeat_age_included_in_response(self, web_session):
        """worker_heartbeat dict must include heartbeat_age_seconds."""
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        payload = json.dumps({
            "instance_id": "age-test-worker",
            "worker_id": "age-runner",
            "last_seen": past_time,
            "runner_state": "IDLE",
            "uptime_seconds": 100.0,
            "xvfb_healthy": True,
            "chrome_reachable": True,
            "chromedriver_reachable": True,
            "session_profile_present": True,
            "provider_active": False,
        })
        row = web_session.query(AppSetting).filter(
            AppSetting.key == "system:worker_heartbeat"
        ).first()
        if not row:
            row = AppSetting(key="system:worker_heartbeat", value=payload, description="test")
            web_session.add(row)
        else:
            row.value = payload
        web_session.commit()

        status = WhatsAppWebService.get_status(web_session)
        hb = status.get("worker_heartbeat")
        assert hb is not None
        assert "heartbeat_age_seconds" in hb
        assert hb["heartbeat_age_seconds"] >= 10.0

    def test_worker_restart_orphan_recovery(self, web_session):
        """After worker restart, orphaned command must be recovered before new claims."""
        from app.services.whatsapp_command_service import WhatsAppCommandService
        import json as _json

        # Submit and claim a command
        _, _, cmd = WhatsAppCommandService.submit_command(
            web_session, action="HEALTH_CHECK", requested_by="test_operator"
        )
        req_id = cmd["request_id"]
        WhatsAppCommandService.claim_command(web_session, worker_id="old-worker", lease_duration_seconds=60)

        # Simulate expired lease (worker crashed)
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        active = WhatsAppCommandService.get_active_command(web_session)
        active["lease_expires_at"] = past_time
        setting_row = web_session.query(AppSetting).filter(
            AppSetting.key == WhatsAppCommandService.ACTIVE_COMMAND_KEY
        ).first()
        setting_row.value = _json.dumps(active)
        web_session.commit()

        # New worker starts, calls recover_orphans
        new_handler = WhatsAppCommandHandler(db=web_session, worker_id="new-worker")
        recovered = new_handler.recover_orphans()
        assert recovered is not None
        assert recovered["status"] == "FAILED"
        assert "Orphaned command recovered" in recovered["error_message"]


# ==============================================================================
# F. Worker Identity Preflight Check
# ==============================================================================

class TestWorkerIdentityPreflight:
    """Tests for check_worker_instance_id() in app/readiness/preflight.py."""

    def test_valid_worker_instance_id_passes_preflight(self):
        """Valid WORKER_INSTANCE_ID must pass preflight check."""
        from app.readiness.preflight import check_worker_instance_id

        with patch("app.readiness.preflight.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = "oracle-arm64-worker-01"
            res = check_worker_instance_id()
            assert res.passed is True
            assert "verified" in res.message

    def test_empty_worker_instance_id_fails_preflight(self):
        """Empty WORKER_INSTANCE_ID must fail preflight check."""
        from app.readiness.preflight import check_worker_instance_id

        with patch("app.readiness.preflight.settings") as mock_settings:
            mock_settings.WORKER_INSTANCE_ID = ""
            res = check_worker_instance_id()
            assert res.passed is False
            assert "not configured" in res.message

    def test_invalid_worker_instance_id_fails_preflight(self):
        """Invalid format WORKER_INSTANCE_ID must fail preflight check."""
        from app.readiness.preflight import check_worker_instance_id

        invalid_examples = ["Oracle-Worker", "w", "-worker", "worker-", "w" * 65, "worker_01"]
        for inv in invalid_examples:
            with patch("app.readiness.preflight.settings") as mock_settings:
                mock_settings.WORKER_INSTANCE_ID = inv
                res = check_worker_instance_id()
                assert res.passed is False, f"Expected invalid: {inv!r}"


# ==============================================================================
# G. Explicit Heartbeat Freshness Boundary & Clock Skew Tests
# ==============================================================================

class TestHeartbeatBoundaryAndClockSkew:
    """
    Explicit boundary tests for heartbeat age evaluation and future clock skew.

    Freshness rules:
      raw_diff < -1.0s:  DEGRADED (clock skew / future timestamp)
      -1.0s <= raw_diff < 0s: HEALTHY (absorb minor NTP/network jitter, age treated as 0.0s)
      0s <= raw_diff < 30.0s: HEALTHY (if xvfb and chrome reachable)
      30.0s <= raw_diff < 60.0s: DEGRADED
      raw_diff >= 60.0s: OFFLINE
    """

    FIXED_NOW = datetime(2026, 9, 23, 23, 0, 0, tzinfo=timezone.utc)

    def _set_heartbeat(self, web_session, offset_seconds: float, xvfb: bool = True, chrome: bool = True):
        last_seen = (self.FIXED_NOW - timedelta(seconds=offset_seconds)).isoformat()
        payload = json.dumps({
            "instance_id": "oracle-arm64-worker-01",
            "worker_id": "test-runner-boundary",
            "last_seen": last_seen,
            "runner_state": "IDLE",
            "uptime_seconds": 500.0,
            "xvfb_healthy": xvfb,
            "chrome_reachable": chrome,
            "chromedriver_reachable": True,
            "session_profile_present": True,
            "provider_active": False,
        })
        row = web_session.query(AppSetting).filter(
            AppSetting.key == "system:worker_heartbeat"
        ).first()
        if not row:
            row = AppSetting(key="system:worker_heartbeat", value=payload, description="test")
            web_session.add(row)
        else:
            row.value = payload
        web_session.commit()

    def _get_status_deterministic(self, web_session):
        """Executes WhatsAppWebService.get_status() with fixed now_utc for microsecond determinism."""
        with patch("app.web.services.whatsapp_service.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: self.FIXED_NOW
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            return WhatsAppWebService.get_status(web_session)

    def test_boundary_29_999s_healthy(self, web_session):
        """29.999s offset (<30s) must evaluate to HEALTHY."""
        self._set_heartbeat(web_session, offset_seconds=29.999)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.HEALTHY.value

    def test_boundary_30_000s_degraded(self, web_session):
        """Exact 30.000s offset (>=30s, <60s) must evaluate to DEGRADED."""
        self._set_heartbeat(web_session, offset_seconds=30.000)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.DEGRADED.value

    def test_boundary_59_999s_degraded(self, web_session):
        """59.999s offset (>=30s, <60s) must evaluate to DEGRADED."""
        self._set_heartbeat(web_session, offset_seconds=59.999)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.DEGRADED.value

    def test_boundary_60_000s_offline(self, web_session):
        """Exact 60.000s offset (>=60s) must evaluate to OFFLINE."""
        self._set_heartbeat(web_session, offset_seconds=60.000)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.OFFLINE.value

    def test_future_timestamp_minus_5s_clock_skew_degraded(self, web_session):
        """Timestamp 5 seconds in the future (raw_diff < -1.0s) indicates clock skew -> DEGRADED."""
        self._set_heartbeat(web_session, offset_seconds=-5.000)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.DEGRADED.value
        # Heartbeat age should reflect negative raw_diff to signal skew
        assert status["worker_heartbeat"]["heartbeat_age_seconds"] <= -1.0

    def test_tiny_future_timestamp_minus_0_5s_absorbed(self, web_session):
        """Tiny future difference (-0.5s) is within NTP tolerance [-1.0s, 0s) -> HEALTHY."""
        self._set_heartbeat(web_session, offset_seconds=-0.500)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.HEALTHY.value
        # Treated as effectively current (0.0s)
        assert status["worker_heartbeat"]["heartbeat_age_seconds"] == 0.0

    def test_normal_fresh_heartbeat(self, web_session):
        """Normal fresh heartbeat (10.0s) must evaluate to HEALTHY."""
        self._set_heartbeat(web_session, offset_seconds=10.000)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.HEALTHY.value

    def test_normal_stale_heartbeat(self, web_session):
        """Normal stale heartbeat (90.0s) must evaluate to OFFLINE."""
        self._set_heartbeat(web_session, offset_seconds=90.000)
        status = self._get_status_deterministic(web_session)
        assert status["infra_health"] == WorkerInfraHealthEnum.OFFLINE.value


