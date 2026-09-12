"""
Brute-force login defense module.

Tracks failed authentication attempts per normalized username and IP address.
Enforces progressive lockout after the configured failure threshold without
leaking whether an account exists.
"""

import json
from datetime import datetime, timezone, timedelta
import threading
from typing import Dict, Tuple, Optional
from sqlalchemy.orm import Session

from app.web.config import web_settings
from app.models.audit_log import AuditLog


class LoginAttemptTracker:
    """Thread-safe in-memory login attempt and lockout manager."""

    def __init__(
        self,
        max_attempts: Optional[int] = None,
        lockout_minutes: Optional[int] = None
    ):
        self.max_attempts = max_attempts or web_settings.WEB_MAX_FAILED_LOGINS
        self.lockout_minutes = lockout_minutes or web_settings.WEB_LOCKOUT_MINUTES
        self._lock = threading.Lock()
        # Structure: key -> {"attempts": int, "first_failed": datetime, "locked_until": Optional[datetime]}
        self._records: Dict[str, dict] = {}

    def _make_key(self, username: str, ip_address: Optional[str]) -> str:
        norm_user = (username or "").strip().lower()
        norm_ip = (ip_address or "unknown").strip()
        return f"{norm_user}:{norm_ip}"

    def is_locked(self, username: str, ip_address: Optional[str]) -> Tuple[bool, Optional[int]]:
        """
        Checks if the username+IP combination is currently locked out.
        Returns: (is_locked: bool, remaining_seconds: Optional[int])
        """
        key = self._make_key(username, ip_address)
        now = datetime.now(timezone.utc)

        with self._lock:
            record = self._records.get(key)
            if not record:
                return False, None

            locked_until = record.get("locked_until")
            if locked_until:
                if now < locked_until:
                    remaining = int((locked_until - now).total_seconds())
                    return True, remaining
                else:
                    # Lockout expired; reset record
                    del self._records[key]
                    return False, None

            return False, None

    def record_failure(
        self,
        username: str,
        ip_address: Optional[str],
        db: Optional[Session] = None
    ) -> Tuple[bool, Optional[int]]:
        """
        Records a failed authentication attempt.
        Returns: (is_now_locked: bool, remaining_seconds: Optional[int])
        """
        key = self._make_key(username, ip_address)
        now = datetime.now(timezone.utc)
        window_duration = timedelta(minutes=self.lockout_minutes)

        with self._lock:
            record = self._records.get(key)
            if not record:
                record = {
                    "attempts": 1,
                    "first_failed": now,
                    "locked_until": None
                }
                self._records[key] = record
            else:
                # Reset if outside sliding window
                if now - record["first_failed"] > window_duration:
                    record["attempts"] = 1
                    record["first_failed"] = now
                    record["locked_until"] = None
                else:
                    record["attempts"] += 1

            # Check if threshold breached
            if record["attempts"] >= self.max_attempts:
                locked_until = now + timedelta(minutes=self.lockout_minutes)
                record["locked_until"] = locked_until
                remaining = int(self.lockout_minutes * 60)

                # Record audit event if database session provided
                if db:
                    try:
                        audit = AuditLog(
                            event_type="ACCOUNT_LOCKED",
                            status="FAILURE",
                            result=json.dumps({
                                "actor": username.strip().lower(),
                                "username": username.strip().lower(),
                                "ip_address": ip_address,
                                "failed_attempts": record["attempts"],
                                "lockout_duration_minutes": self.lockout_minutes
                            }),
                            error_message="Account temporarily locked due to excessive failed attempts.",
                            created_at=now
                        )
                        db.add(audit)
                        db.commit()
                    except Exception:
                        db.rollback()

                return True, remaining

            return False, None

    def record_success(self, username: str, ip_address: Optional[str]) -> None:
        """Clears failed attempts upon successful login."""
        key = self._make_key(username, ip_address)
        with self._lock:
            self._records.pop(key, None)


# Global login tracker instance
login_tracker = LoginAttemptTracker()
