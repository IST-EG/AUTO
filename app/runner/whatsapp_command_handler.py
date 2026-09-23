"""
Worker-side WhatsApp Command Handler.

Enables ProductionRunner on the Worker VPS to poll, claim, execute, and report
outcomes for operational commands issued by the Web Control Plane.
Preserves physical plane separation: browser and Selenium execution remain 100% on the Worker.

Phase 7.7-B Step 7 extensions:
- publish_identity(): writes stable worker identity to system:worker_identity
- publish_telemetry(): extended to also write infrastructure health to system:worker_heartbeat
- _execute_preflight(): PREFLIGHT command (no Chrome cold-start, 120-second lease)
"""

import os
import re
import sys
import json
import shutil
import logging
import platform
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.services.whatsapp_command_service import WhatsAppCommandService
from app.utils.settings import settings

logger = logging.getLogger(__name__)

# Validation pattern for WORKER_INSTANCE_ID: lowercase, digits, hyphens; 3-64 chars.
_WORKER_INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$|^[a-z0-9]{3,64}$")


def validate_worker_instance_id(instance_id: str) -> bool:
    """
    Validates the WORKER_INSTANCE_ID format.

    Rules:
    - [a-z0-9-] characters only
    - 3 to 64 characters inclusive
    - Must not start or end with a hyphen
    - May not be empty
    Returns True if valid, False otherwise.
    """
    if not isinstance(instance_id, str) or not instance_id:
        return False
    if len(instance_id) < 3 or len(instance_id) > 64:
        return False
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]{1}", instance_id):
        return False
    # Reject pure digit strings (must have at least one letter or hyphen for meaningfulness)
    # This is intentionally not enforced - pure digits like "001" are technically valid
    return True


class WhatsAppCommandHandler:
    """Worker daemon handler executing operational WhatsApp commands."""

    TELEMETRY_SETTING_KEY = "system:whatsapp_telemetry"
    IDENTITY_SETTING_KEY = "system:worker_identity"
    HEARTBEAT_SETTING_KEY = "system:worker_heartbeat"
    PREFLIGHT_RESULT_KEY = "system:last_preflight_result"

    PREFLIGHT_LEASE_SECONDS = 120
    DEFAULT_LEASE_SECONDS = 60

    def __init__(self, db: Session, worker_id: str, provider: Optional[Any] = None):
        self.db = db
        self.worker_id = worker_id
        self.provider = provider
        self._started_at = datetime.now(timezone.utc)

    def recover_orphans(self) -> Optional[Dict[str, Any]]:
        """Recovers any stale or orphaned commands left over from a previous crash."""
        return WhatsAppCommandService.recover_stale_or_orphaned_command(
            db=self.db,
            worker_id=self.worker_id,
        )

    def publish_identity(self) -> None:
        """
        Publishes stable worker identity metadata to system:worker_identity.

        Safe fields only: instance label, platform, runtime, capability list,
        and app version.  No credentials, no session data, no filesystem paths
        beyond capability flags.
        """
        now_utc = datetime.now(timezone.utc)
        raw_id = getattr(settings, "WORKER_INSTANCE_ID", "")
        if isinstance(raw_id, str) and raw_id.strip() and validate_worker_instance_id(raw_id.strip()):
            instance_id = raw_id.strip()
        else:
            instance_id = "unconfigured"
            logger.warning(
                "WORKER_INSTANCE_ID is empty or invalid. "
                "Persisting instance_id='unconfigured'. Ephemeral worker_id must not be used."
            )

        # Chrome version (read from binary, non-blocking)
        chrome_version = self._read_chrome_version()
        # ChromeDriver version
        chromedriver_version = self._read_chromedriver_version()

        identity_data: Dict[str, Any] = {
            "instance_id": instance_id,
            "environment": getattr(settings, "APP_ENV", "") or "production",
            "platform": "Oracle Cloud Always Free A1 Flex" if "aarch64" in platform.machine().lower() else platform.system(),
            "arch": platform.machine(),
            "os": f"{platform.system()} {platform.release()}",
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "chrome_binary_configured": bool(getattr(settings, "WHATSAPP_CHROME_BINARY", "")),
            "chrome_version": chrome_version,
            "chromedriver_version": chromedriver_version,
            "xvfb_display": os.environ.get("DISPLAY", ""),
            "capabilities": self._get_capabilities(),
            "app_version": "Phase 7.7-B",
            "registered_at": self._started_at.isoformat(),
            "last_seen": now_utc.isoformat(),
            "worker_id": self.worker_id,
        }

        self._write_setting(self.IDENTITY_SETTING_KEY, identity_data, "Stable worker instance identity metadata")

    def publish_telemetry(self, state: Optional[str] = None, diagnostic_snippet: Optional[str] = None) -> None:
        """
        Publishes live provider state and diagnostic heartbeat to app_settings.

        Extended (Phase 7.7-B Step 7) to also write infrastructure health snapshot
        to system:worker_heartbeat independently of the session-specific telemetry.
        """
        now_utc = datetime.now(timezone.utc)

        # --- Resolve provider session state ---
        current_state = state
        if not current_state and self.provider:
            session_mgr = getattr(self.provider, "session_manager", None)
            if session_mgr:
                raw_state = getattr(session_mgr, "state", None)
                if hasattr(raw_state, "value") and isinstance(raw_state.value, str):
                    current_state = raw_state.value
                elif isinstance(raw_state, str):
                    current_state = raw_state

        if not isinstance(current_state, str):
            current_state = "CONNECTED" if self.provider else "DISCONNECTED"

        # --- Resolve diagnostic snippet ---
        snippet = diagnostic_snippet
        if not snippet and self.provider:
            browser = getattr(self.provider, "browser", None)
            if browser:
                try:
                    raw_snippet = browser.capture_diagnostic_snippet()
                    if isinstance(raw_snippet, str):
                        snippet = raw_snippet
                except Exception:
                    pass
        if not isinstance(snippet, str):
            snippet = None

        # --- Write session telemetry (existing key, unchanged schema) ---
        telemetry_data = {
            "state": current_state,
            "last_health_check": now_utc.isoformat(),
            "diagnostic_snippet": snippet,
            "worker_id": str(self.worker_id),
            "updated_at": now_utc.isoformat(),
        }
        self._write_setting(self.TELEMETRY_SETTING_KEY, telemetry_data, "Live WhatsApp Web provider telemetry reported by active worker")

        # --- Write infrastructure heartbeat (new key, Phase 7.7-B Step 7) ---
        self._publish_worker_heartbeat(current_state, now_utc)

    def _publish_worker_heartbeat(self, runner_state: str, now_utc: Optional[datetime] = None) -> None:
        """
        Writes infrastructure health snapshot to system:worker_heartbeat.
        Safe fields only. No credentials, no session data, no message bodies.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        uptime_seconds = (now_utc - self._started_at).total_seconds()
        raw_id = getattr(settings, "WORKER_INSTANCE_ID", "")
        if isinstance(raw_id, str) and raw_id.strip() and validate_worker_instance_id(raw_id.strip()):
            instance_id = raw_id.strip()
        else:
            instance_id = "unconfigured"

        heartbeat_data: Dict[str, Any] = {
            "instance_id": instance_id,
            "worker_id": self.worker_id,
            "last_seen": now_utc.isoformat(),
            "runner_state": runner_state,
            "uptime_seconds": round(uptime_seconds, 1),
            "xvfb_healthy": self._check_xvfb(),
            "chrome_reachable": self._check_chrome(),
            "chromedriver_reachable": self._check_chromedriver(),
            "session_profile_present": self._check_session_profile(),
            "provider_active": self.provider is not None,
        }

        self._write_setting(self.HEARTBEAT_SETTING_KEY, heartbeat_data, "Worker infrastructure health heartbeat (Phase 7.7-B Step 7)")

    # ------------------------------------------------------------------
    # Infrastructure health probe helpers (non-blocking, no side effects)
    # ------------------------------------------------------------------

    def _check_xvfb(self) -> bool:
        """Returns True if Xvfb display is configured and xdpyinfo succeeds."""
        display = os.environ.get("DISPLAY", "")
        if not display:
            return False
        try:
            import subprocess
            result = subprocess.run(
                ["xdpyinfo", "-display", display],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_chrome(self) -> bool:
        """Returns True if Chrome binary is discoverable (non-launching)."""
        chrome_binary = getattr(settings, "WHATSAPP_CHROME_BINARY", "")
        if chrome_binary and os.path.isfile(chrome_binary):
            return True
        return bool(shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium-browser"))

    def _check_chromedriver(self) -> bool:
        """Returns True if ChromeDriver binary is discoverable (non-launching)."""
        chromedriver_path = getattr(settings, "WHATSAPP_CHROMEDRIVER_PATH", "")
        if chromedriver_path and os.path.isfile(chromedriver_path):
            return True
        return bool(shutil.which("chromedriver"))

    def _check_session_profile(self) -> bool:
        """Returns True if the WhatsApp session profile directory contains data."""
        session_path = getattr(settings, "WHATSAPP_SESSION_PATH", "./data/whatsapp_session")
        try:
            import pathlib
            p = pathlib.Path(session_path)
            return p.is_dir() and any(p.iterdir())
        except Exception:
            return False

    def _read_chrome_version(self) -> Optional[str]:
        """Reads Chrome version string non-destructively (no browser launch)."""
        chrome_binary = getattr(settings, "WHATSAPP_CHROME_BINARY", "")
        binary = chrome_binary if (chrome_binary and os.path.isfile(chrome_binary)) else (
            shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium-browser")
        )
        if not binary:
            return None
        try:
            import subprocess
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _read_chromedriver_version(self) -> Optional[str]:
        """Reads ChromeDriver version string non-destructively (no launch)."""
        chromedriver_path = getattr(settings, "WHATSAPP_CHROMEDRIVER_PATH", "")
        binary = chromedriver_path if (chromedriver_path and os.path.isfile(chromedriver_path)) else shutil.which("chromedriver")
        if not binary:
            return None
        try:
            import subprocess
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _get_capabilities(self) -> list:
        """Returns list of confirmed worker capabilities (non-launching checks)."""
        caps = []
        if self._check_chrome():
            caps.append("chrome_for_testing")
        if self._check_chromedriver():
            caps.append("chromedriver")
        if self._check_xvfb():
            caps.append("xvfb_display")
        if self._check_session_profile():
            caps.append("whatsapp_session_profile")
        caps.append("whatsapp_web_automation")
        return caps

    # ------------------------------------------------------------------
    # AppSetting write helper
    # ------------------------------------------------------------------

    def _write_setting(self, key: str, data: Dict[str, Any], description: str) -> None:
        """Atomically writes a JSON payload to an AppSetting key."""
        now_utc = datetime.now(timezone.utc)
        try:
            payload = json.dumps(data)
        except Exception as e:
            logger.warning(f"Failed to serialize payload for {key}: {e}")
            return

        try:
            row = self.db.query(AppSetting).filter(AppSetting.key == key).first()
            if not row:
                row = AppSetting(
                    key=key,
                    value=payload,
                    description=description,
                    updated_at=now_utc,
                )
                self.db.add(row)
            else:
                row.value = payload
                row.updated_at = now_utc
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"Failed to write AppSetting [{key}]: {e}")

    # ------------------------------------------------------------------
    # Command poll & dispatch
    # ------------------------------------------------------------------

    def poll_and_execute(self) -> Optional[Dict[str, Any]]:
        """
        Polls for an active command in REQUESTED state.
        If found, atomically claims the lease via CAS and executes the operation.
        PREFLIGHT uses a 120-second lease; all other commands use 60 seconds.
        """
        # Peek at the action before claiming to determine lease duration
        active = WhatsAppCommandService.get_active_command(self.db)
        if active and active.get("status") == "REQUESTED":
            action = active.get("action", "")
            lease_seconds = self.PREFLIGHT_LEASE_SECONDS if action == "PREFLIGHT" else self.DEFAULT_LEASE_SECONDS
        else:
            lease_seconds = self.DEFAULT_LEASE_SECONDS

        claimed = WhatsAppCommandService.claim_command(
            db=self.db,
            worker_id=self.worker_id,
            lease_duration_seconds=lease_seconds,
        )
        if not claimed:
            return None

        request_id = claimed.get("request_id")
        action = claimed.get("action")
        params = claimed.get("params", {})

        logger.info(f"Worker {self.worker_id} executing WhatsApp command '{action}' (request_id={request_id})")
        WhatsAppCommandService.mark_executing(self.db, request_id, self.worker_id)

        try:
            if action == "HEALTH_CHECK":
                self._execute_health_check(request_id)
            elif action == "RECONNECT":
                self._execute_reconnect(request_id, params)
            elif action == "DISCONNECT":
                self._execute_disconnect(request_id, params)
            elif action == "LOGOUT":
                self._execute_logout(request_id, params)
            elif action == "PREFLIGHT":
                self._execute_preflight(request_id, params)
            else:
                WhatsAppCommandService.fail_command(
                    db=self.db,
                    request_id=request_id,
                    worker_id=self.worker_id,
                    error_message=f"Unrecognized action '{action}'",
                )
            return claimed

        except Exception as e:
            logger.error(f"Error executing WhatsApp command '{action}': {e}", exc_info=True)
            WhatsAppCommandService.fail_command(
                db=self.db,
                request_id=request_id,
                worker_id=self.worker_id,
                error_message=str(e),
            )
            return claimed

    # ------------------------------------------------------------------
    # Command execution methods
    # ------------------------------------------------------------------

    def _execute_health_check(self, request_id: str) -> None:
        if not self.provider:
            WhatsAppCommandService.complete_command(
                db=self.db,
                request_id=request_id,
                worker_id=self.worker_id,
                result="HEALTHY (Simulated/Ready)",
            )
            return

        is_healthy = False
        try:
            is_healthy = self.provider.health_check()
        except Exception as e:
            logger.warning(f"Health check exception: {e}")

        self.publish_telemetry()

        if is_healthy:
            WhatsAppCommandService.complete_command(
                db=self.db,
                request_id=request_id,
                worker_id=self.worker_id,
                result="HEALTHY (Chat interface verified responsive)",
            )
        else:
            WhatsAppCommandService.complete_command(
                db=self.db,
                request_id=request_id,
                worker_id=self.worker_id,
                result="DEGRADED (Session not ready or QR scan required)",
            )

    def _execute_reconnect(self, request_id: str, params: Dict[str, Any]) -> None:
        if self.provider and hasattr(self.provider, "session_manager"):
            restarted = self.provider.session_manager.restart_session()
            self.publish_telemetry()
            if restarted:
                WhatsAppCommandService.complete_command(
                    db=self.db,
                    request_id=request_id,
                    worker_id=self.worker_id,
                    result="Session restored cleanly from cached profile",
                )
            else:
                WhatsAppCommandService.fail_command(
                    db=self.db,
                    request_id=request_id,
                    worker_id=self.worker_id,
                    error_message="Browser restart succeeded but session requires QR scan",
                )
        else:
            # Standalone / mock provider recovery
            self.publish_telemetry(state="CONNECTED")
            WhatsAppCommandService.complete_command(
                db=self.db,
                request_id=request_id,
                worker_id=self.worker_id,
                result="Provider reconnected successfully",
            )

    def _execute_disconnect(self, request_id: str, params: Dict[str, Any]) -> None:
        if self.provider and hasattr(self.provider, "disconnect"):
            try:
                self.provider.disconnect()
            except Exception as e:
                logger.warning(f"Error during provider disconnect: {e}")

        self.publish_telemetry(state="STOPPED")
        WhatsAppCommandService.complete_command(
            db=self.db,
            request_id=request_id,
            worker_id=self.worker_id,
            result="Browser session disconnected gracefully",
        )

    def _execute_logout(self, request_id: str, params: Dict[str, Any]) -> None:
        if self.provider and hasattr(self.provider, "session_manager"):
            try:
                self.provider.session_manager.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down session for logout: {e}")

        clear_cache = params.get("clear_cache", False)
        if clear_cache:
            import shutil as _shutil
            session_path = getattr(settings, "WHATSAPP_SESSION_PATH", "./data/whatsapp_session")
            if os.path.exists(session_path):
                try:
                    _shutil.rmtree(session_path)
                    logger.info(f"Cleared persistent session cache at: {session_path}")
                except Exception as e:
                    logger.warning(f"Could not remove session cache directory: {e}")

        self.publish_telemetry(state="DISCONNECTED")
        WhatsAppCommandService.complete_command(
            db=self.db,
            request_id=request_id,
            worker_id=self.worker_id,
            result=f"Controlled logout completed (clear_cache={clear_cache})",
        )

    def _execute_preflight(self, request_id: str, params: Dict[str, Any]) -> None:
        """
        Executes a safe infrastructure preflight check.

        SAFETY CONSTRAINTS (strictly enforced):
        - MUST NOT cold-start Chrome.
        - MUST NOT launch WhatsApp Web.
        - MUST NOT send messages.
        - MUST NOT initiate a browser session.
        - If a browser/provider is already active (self.provider is not None),
          it may be queried for health status, but no new browser is opened.
        - Infrastructure checks (Xvfb, Chrome binary presence, profile storage) are
          always run regardless of provider state.

        Results are persisted to system:last_preflight_result and returned as COMPLETED.
        """
        from app.readiness.preflight import run_preflight

        now_utc = datetime.now(timezone.utc)
        logger.info(f"PREFLIGHT command executing (request_id={request_id}) — NO Chrome cold-start")

        # --- Gather infrastructure checks (never launches browser) ---
        infra_checks = {
            "xvfb_healthy": self._check_xvfb(),
            "chrome_reachable": self._check_chrome(),
            "chromedriver_reachable": self._check_chromedriver(),
            "session_profile_present": self._check_session_profile(),
            "provider_active": self.provider is not None,
        }

        # --- Run preflight engine ---
        # Pass the active provider (or None). The preflight engine will:
        # - If provider is None: check browser environment by binary presence only
        #   (check_browser_environment uses shutil.which — no launch).
        # - If provider is not None: assume browser env is OK (provider already running).
        # The session_authentication check uses only filesystem inspection; no browser launch.
        try:
            preflight_result = run_preflight(
                db=self.db,
                campaign_id=None,  # No campaign-specific checks
                strict=False,      # session auth is non-critical in diagnostic mode
                provider=self.provider if self.provider is not None else None,
            )
            checks_data = preflight_result.to_dict()
        except Exception as e:
            logger.error(f"PREFLIGHT run_preflight() raised exception: {e}", exc_info=True)
            checks_data = {"passed": False, "error": str(e), "checks": []}

        # --- Compile result payload ---
        result_payload = {
            "executed_at": now_utc.isoformat(),
            "worker_id": self.worker_id,
            "infra_checks": infra_checks,
            "preflight": checks_data,
            "chrome_cold_started": False,   # Invariant: always False
            "whatsapp_launched": False,      # Invariant: always False
            "messages_sent": 0,              # Invariant: always 0
        }

        # Persist result
        self._write_setting(
            self.PREFLIGHT_RESULT_KEY,
            result_payload,
            "Last PREFLIGHT command result (no Chrome cold-start, no WhatsApp launch)",
        )

        overall_passed = checks_data.get("passed", False)
        summary = "PASS" if overall_passed else "PARTIAL"
        infra_ok = all([
            infra_checks["chrome_reachable"],
            infra_checks["session_profile_present"],
        ])
        result_str = (
            f"PREFLIGHT {summary}: infra_ok={infra_ok}, "
            f"xvfb={infra_checks['xvfb_healthy']}, "
            f"chrome={infra_checks['chrome_reachable']}, "
            f"profile={infra_checks['session_profile_present']}, "
            f"provider_active={infra_checks['provider_active']}"
        )

        WhatsAppCommandService.complete_command(
            db=self.db,
            request_id=request_id,
            worker_id=self.worker_id,
            result=result_str,
            details={"infra_checks": infra_checks, "overall_preflight_passed": overall_passed},
        )

        logger.info(f"PREFLIGHT complete: {result_str} (request_id={request_id})")
