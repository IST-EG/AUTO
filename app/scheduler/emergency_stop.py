"""
Emergency Stop controller.

Target emergency-stop latency is <500ms under normal worker conditions and must be verified through automated integration tests.
Guarantees clean shutdown at safe cancellation points without forcibly aborting in-flight provider calls.
"""

from typing import Optional
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog


class EmergencyStopTriggered(Exception):
    """Raised when worker hits a safe cancellation point during emergency stop."""
    pass


class EmergencyStop:
    """
    System-wide emergency stop mechanism persisted in AppSetting.
    """

    SETTING_KEY = "system:emergency_stop"

    def __init__(self, db: Session):
        self.db = db
        self._memory_flag: Optional[bool] = None

    def is_active(self) -> bool:
        """
        Fast check whether emergency stop is active.
        Reads in-memory cache if available; falls back to DB.
        """
        if self._memory_flag is not None:
            return self._memory_flag

        setting = self.db.query(AppSetting).filter(AppSetting.key == self.SETTING_KEY).first()
        active = setting is not None and setting.value.strip().lower() in ("true", "1", "yes")
        self._memory_flag = active
        return active

    def trigger(self, reason: str = "Operator triggered emergency stop") -> None:
        """
        Activates emergency stop globally.
        """
        self._memory_flag = True

        setting = self.db.query(AppSetting).filter(AppSetting.key == self.SETTING_KEY).first()
        if not setting:
            setting = AppSetting(key=self.SETTING_KEY, value="true", description="System Emergency Stop Flag")
            self.db.add(setting)
        else:
            setting.value = "true"

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
        self._memory_flag = False

        setting = self.db.query(AppSetting).filter(AppSetting.key == self.SETTING_KEY).first()
        if setting:
            setting.value = "false"

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
