"""
Worker-side WhatsApp Command Handler.

Enables ProductionRunner on the Worker VPS to poll, claim, execute, and report
outcomes for operational commands issued by the Web Control Plane.
Preserves physical plane separation: browser and Selenium execution remain 100% on the Worker.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.services.whatsapp_command_service import WhatsAppCommandService
from app.utils.settings import settings

logger = logging.getLogger(__name__)


class WhatsAppCommandHandler:
    """Worker daemon handler executing operational WhatsApp commands."""

    TELEMETRY_SETTING_KEY = "system:whatsapp_telemetry"

    def __init__(self, db: Session, worker_id: str, provider: Optional[Any] = None):
        self.db = db
        self.worker_id = worker_id
        self.provider = provider

    def recover_orphans(self) -> Optional[Dict[str, Any]]:
        """Recovers any stale or orphaned commands left over from a previous crash."""
        return WhatsAppCommandService.recover_stale_or_orphaned_command(
            db=self.db,
            worker_id=self.worker_id,
        )

    def publish_telemetry(self, state: Optional[str] = None, diagnostic_snippet: Optional[str] = None) -> None:
        """Publishes live provider state and diagnostic heartbeat to app_settings."""
        now_utc = datetime.now(timezone.utc)
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

        telemetry_data = {
            "state": current_state,
            "last_health_check": now_utc.isoformat(),
            "diagnostic_snippet": snippet,
            "worker_id": str(self.worker_id),
            "updated_at": now_utc.isoformat(),
        }

        try:
            payload = json.dumps(telemetry_data)
        except Exception as e:
            logger.warning(f"Failed to serialize WhatsApp telemetry payload: {e}")
            return

        try:
            row = self.db.query(AppSetting).filter(AppSetting.key == self.TELEMETRY_SETTING_KEY).first()
            if not row:
                row = AppSetting(
                    key=self.TELEMETRY_SETTING_KEY,
                    value=payload,
                    description="Live WhatsApp Web provider telemetry reported by active worker",
                    updated_at=now_utc,
                )
                self.db.add(row)
            else:
                row.value = payload
                row.updated_at = now_utc
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"Failed to publish WhatsApp telemetry: {e}")

    def poll_and_execute(self) -> Optional[Dict[str, Any]]:
        """
        Polls for an active command in REQUESTED state.
        If found, atomically claims the lease via CAS and executes the operation.
        """
        claimed = WhatsAppCommandService.claim_command(
            db=self.db,
            worker_id=self.worker_id,
            lease_duration_seconds=60,
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

        res_str = "HEALTHY" if is_healthy else "UNHEALTHY"
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
            import shutil
            session_path = getattr(settings, "WHATSAPP_SESSION_PATH", "./data/whatsapp_session")
            if os.path.exists(session_path):
                try:
                    shutil.rmtree(session_path)
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
