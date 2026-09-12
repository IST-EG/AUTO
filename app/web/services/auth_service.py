"""
Authentication Service for Web Control Center.

Coordinates user credential verification, brute-force defense tracking,
session creation/rotation, audit logging, and logout.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.audit_log import AuditLog
from app.web.security.hasher import default_password_hasher
from app.web.security.session import session_manager
from app.web.security.brute_force import login_tracker


class AuthService:
    """Orchestrates authentication, session issuance, and logout."""

    @classmethod
    def authenticate(
        cls,
        db: Session,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        old_token: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str], Optional[Dict[str, Any]]]:
        """
        Authenticates a user and issues a session token.

        Returns:
            (success: bool, message: str, session_token: Optional[str], user_dict: Optional[dict])
        """
        norm_user = (username or "").strip().lower()

        # 1. Check brute-force lockout
        is_locked, remaining_secs = login_tracker.is_locked(norm_user, ip_address)
        if is_locked:
            return False, f"Account temporarily locked due to repeated failed attempts. Try again in {remaining_secs} seconds.", None, None

        # 2. Query user
        user = db.query(User).filter(User.username == norm_user).first()
        now = datetime.now(timezone.utc)

        if not user or not user.is_active:
            # Record failure without leaking user existence
            is_locked_now, _ = login_tracker.record_failure(norm_user, ip_address, db=db)
            return False, "Invalid username or password.", None, None

        # 3. Verify password
        if not default_password_hasher.verify(password, user.password_hash):
            login_tracker.record_failure(norm_user, ip_address, db=db)
            audit = AuditLog(
                event_type="USER_LOGIN_FAILED",
                status="FAILURE",
                result=json.dumps({"actor": norm_user, "username": norm_user, "ip_address": ip_address}),
                error_message="Invalid username or password.",
                created_at=now
            )
            db.add(audit)
            db.commit()
            return False, "Invalid username or password.", None, None

        # 4. Successful authentication - clear failures
        login_tracker.record_success(norm_user, ip_address)

        # 5. Transparent work-factor upgrade if needed
        if default_password_hasher.needs_rehash(user.password_hash):
            user.password_hash = default_password_hasher.hash(password)

        # 6. Update last login
        user.last_login_at = now
        db.commit()

        # 7. Create stateful session (rotates old_token and enforces max 2 sessions)
        raw_token = session_manager.create_session(
            db=db,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            old_token=old_token
        )

        # 8. Record audit log
        audit = AuditLog(
            event_type="USER_LOGIN_SUCCESS",
            status="SUCCESS",
            result=json.dumps({"actor": norm_user, "user_id": user.id, "username": norm_user, "role": user.role, "ip_address": ip_address}),
            created_at=now
        )
        db.add(audit)
        db.commit()

        user_info = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role
        }

        return True, "Login successful.", raw_token, user_info

    @classmethod
    def logout(
        cls,
        db: Session,
        raw_token: str,
        user: Optional[User] = None
    ) -> bool:
        """Revokes active session and logs audit event."""
        if not raw_token:
            return False

        success = session_manager.invalidate_session(db, raw_token)
        if success and user:
            audit = AuditLog(
                event_type="USER_LOGOUT",
                status="SUCCESS",
                result=json.dumps({"actor": user.username, "user_id": user.id, "username": user.username}),
                created_at=datetime.now(timezone.utc)
            )
            db.add(audit)
            db.commit()

        return success
