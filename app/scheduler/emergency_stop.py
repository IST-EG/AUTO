"""
Emergency Stop controller.

Target emergency-stop latency is <500ms under normal worker conditions and must be verified through automated integration tests.
Guarantees clean shutdown at safe cancellation points without forcibly aborting in-flight provider calls.
"""

import json
import time
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog


class EmergencyStopTriggered(Exception):
    """Raised when worker hits a safe cancellation point during emergency stop."""
    pass


class EmergencyStop:
    """
    System-wide emergency stop mechanism persisted in AppSetting.
    Canonical key: 'system:emergency_stop'
    """

    SETTING_KEY = "system:emergency_stop"
    CACHE_TTL_SECONDS = 1.0

    def __init__(self, db: Session):
        self.db = db
        self._cached_flag: Optional[bool] = None
        self._last_checked_mono: float = 0.0

    @property
    def _memory_flag(self) -> Optional[bool]:
        """Backward compatibility accessor for in-memory flag."""
        return self._cached_flag

    @_memory_flag.setter
    def _memory_flag(self, val: Optional[bool]):
        self._cached_flag = val
        self._last_checked_mono = time.monotonic()

    @classmethod
    def parse_setting_value(cls, raw_val: Optional[str]) -> bool:
        """
        Authoritative parser for emergency stop boolean representation.
        Handles canonical strings ('true', '1', 'yes') and defensively tolerates legacy JSON.
        """
        if not raw_val:
            return False
        cleaned = raw_val.strip().lower()
        if cleaned in ("true", "1", "yes"):
            return True
        if cleaned.startswith("{") and "active" in cleaned:
            try:
                data = json.loads(raw_val)
                return bool(data.get("active", False))
            except Exception:
                pass
        return False

    @classmethod
    def get_status(cls, db: Session) -> Dict[str, Any]:
        """
        Authoritative system emergency stop status reader.
        Returns a unified dictionary:
            {
                'active': bool,
                'reason': Optional[str],
                'activated_at': Optional[str]
            }
        """
        setting = db.query(AppSetting).filter(AppSetting.key == cls.SETTING_KEY).first()
        is_act = setting is not None and cls.parse_setting_value(setting.value)
        reason: Optional[str] = None
        activated_at: Optional[str] = None

        if is_act and setting:
            reason = setting.description or "Operator triggered emergency stop"
            if setting.updated_at:
                activated_at = setting.updated_at.isoformat()
            else:
                last_log = (
                    db.query(AuditLog)
                    .filter(AuditLog.event_type == "EMERGENCY_STOP_ACTIVATED")
                    .order_by(AuditLog.id.desc())
                    .first()
                )
                if last_log and last_log.created_at:
                    activated_at = last_log.created_at.isoformat()
                    if not reason:
                        reason = last_log.error_message

        return {
            "active": is_act,
            "reason": reason,
            "activated_at": activated_at,
        }

    def is_active(self, force_refresh: bool = False) -> bool:
        """
        Fast check whether emergency stop is active.
        Caches in-memory for CACHE_TTL_SECONDS (1.0s) to avoid query amplification,
        while ensuring running workers detect activations without process restart.
        """
        now_mono = time.monotonic()
        if not force_refresh and self._cached_flag is not None:
            if (now_mono - self._last_checked_mono) < self.CACHE_TTL_SECONDS:
                return self._cached_flag

        status = self.get_status(self.db)
        self._cached_flag = status["active"]
        self._last_checked_mono = now_mono
        return self._cached_flag

    def trigger(self, reason: str = "Operator triggered emergency stop") -> None:
        """
        Activates emergency stop globally.
        Updates DB setting, records audit log, commits, and updates local cache.
        """
        self._cached_flag = True
        self._last_checked_mono = time.monotonic()

        setting = self.db.query(AppSetting).filter(AppSetting.key == self.SETTING_KEY).first()
        if not setting:
            setting = AppSetting(key=self.SETTING_KEY, value="true", description=reason)
            self.db.add(setting)
        else:
            setting.value = "true"
            setting.description = reason

        # Create audit log
        log = AuditLog(
            event_type="EMERGENCY_STOP_ACTIVATED",
            status="STOPPED",
            error_message=reason
        )
        self.db.add(log)
        self.db.commit()

    def resume(self, reason: str = "Operator resumed system") -> None:
        """
        Deactivates emergency stop and permits worker execution to resume.
        """
        self._cached_flag = False
        self._last_checked_mono = time.monotonic()

        setting = self.db.query(AppSetting).filter(AppSetting.key == self.SETTING_KEY).first()
        if setting:
            setting.value = "false"
            setting.description = reason

        log = AuditLog(
            event_type="EMERGENCY_STOP_RESUMED",
            status="ACTIVE",
            result=reason
        )
        self.db.add(log)
        self.db.commit()

    def check_safe_cancellation_point(self) -> None:
        """
        Safe point check. Raises EmergencyStopTriggered if emergency stop is engaged.
        Workers invoke this before claiming queue items and between processing steps.
        """
        if self.is_active():
            raise EmergencyStopTriggered("Operation aborted at safe cancellation point due to emergency stop.")
