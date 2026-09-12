"""
Session security and lifecycle management.

Handles cryptographically secure token generation, hashing, session rotation,
concurrent session enforcement (max 2 active), sliding inactivity timeouts,
and server-side invalidation.
"""

import json
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_session import UserSession
from app.models.audit_log import AuditLog
from app.web.config import web_settings


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensures datetime instance is timezone-aware in UTC (SQLite naive datetime compatibility)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def hash_session_token(raw_token: str) -> str:
    """Computes SHA-256 hex digest of a raw session token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class SessionManager:
    """Manages creation, verification, and revocation of user sessions."""

    def __init__(
        self,
        inactivity_minutes: Optional[int] = None,
        absolute_hours: Optional[int] = None,
        max_concurrent: Optional[int] = None
    ):
        self.inactivity_minutes = inactivity_minutes or web_settings.WEB_SESSION_INACTIVITY_MINUTES
        self.absolute_hours = absolute_hours or web_settings.WEB_SESSION_ABSOLUTE_HOURS
        self.max_concurrent = max_concurrent or web_settings.WEB_MAX_CONCURRENT_SESSIONS

    def create_session(
        self,
        db: Session,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        old_token: Optional[str] = None
    ) -> str:
        """
        Creates a new authenticated session for a user.

        Security Behaviors:
            1. Session Rotation: If old_token is supplied, it is invalidated (anti-fixation).
            2. Concurrent Limits: Enforces max concurrent active sessions. If exceeded,
               the oldest session is revoked and an audit event is emitted.
            3. Hashed Storage: Only SHA-256 hashes are stored in the database.
        """
        now = datetime.now(timezone.utc)

        # 1. Invalidate previous session if provided (rotation on login)
        if old_token:
            old_hash = hash_session_token(old_token)
            db.query(UserSession).filter(UserSession.session_token_hash == old_hash).delete()

        # 2. Check and enforce concurrent session limit
        active_sessions = db.query(UserSession).filter(
            UserSession.user_id == user.id
        ).order_by(UserSession.created_at.asc()).all()

        valid_sessions = [s for s in active_sessions if _ensure_utc(s.expires_at) and _ensure_utc(s.expires_at) > now]

        if len(valid_sessions) >= self.max_concurrent:
            # Revoke oldest sessions until count < max_concurrent
            excess_count = len(valid_sessions) - self.max_concurrent + 1
            for i in range(excess_count):
                session_to_evict = valid_sessions[i]
                db.delete(session_to_evict)

                # Record audit log for concurrent session eviction
                audit = AuditLog(
                    event_type="SESSION_REVOKED_CONCURRENT_LIMIT",
                    status="SUCCESS",
                    result=json.dumps({
                        "actor": user.username,
                        "user_id": user.id,
                        "revoked_session_id": session_to_evict.id,
                        "reason": f"Exceeded maximum concurrent sessions ({self.max_concurrent})"
                    }),
                    created_at=now
                )
                db.add(audit)

        # 3. Generate raw token and its SHA-256 hash
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_session_token(raw_token)

        # 4. Insert new session record
        expires_at = now + timedelta(hours=self.absolute_hours)
        new_session = UserSession(
            id=str(uuid.uuid4()),
            user_id=user.id,
            session_token_hash=token_hash,
            ip_address=ip_address,
            user_agent=user_agent[:255] if user_agent else None,
            expires_at=expires_at,
            created_at=now,
            last_active_at=now
        )
        db.add(new_session)
        db.commit()

        return raw_token

    def get_user_from_token(
        self,
        db: Session,
        raw_token: str
    ) -> Optional[Tuple[User, UserSession]]:
        """
        Validates raw_token against active sessions.

        Checks:
            - Token existence in database
            - User active status
            - Absolute expiration threshold
            - Sliding inactivity threshold
        """
        if not raw_token:
            return None

        token_hash = hash_session_token(raw_token)
        session_record = db.query(UserSession).filter(
            UserSession.session_token_hash == token_hash
        ).first()

        if not session_record:
            return None

        now = datetime.now(timezone.utc)

        # Check absolute expiration
        expires_at = _ensure_utc(session_record.expires_at)
        if expires_at and expires_at <= now:
            db.delete(session_record)
            db.commit()
            return None

        # Check sliding inactivity timeout
        last_active = _ensure_utc(session_record.last_active_at)
        inactivity_limit = timedelta(minutes=self.inactivity_minutes)
        if last_active and (now - last_active > inactivity_limit):
            db.delete(session_record)
            db.commit()
            return None

        user = db.query(User).filter(User.id == session_record.user_id).first()
        if not user or not user.is_active:
            db.delete(session_record)
            db.commit()
            return None

        # Update last_active_at timestamp (sliding window)
        session_record.last_active_at = now
        db.commit()

        return user, session_record

    def invalidate_session(self, db: Session, raw_token: str) -> bool:
        """Deletes session from database upon logout."""
        if not raw_token:
            return False

        token_hash = hash_session_token(raw_token)
        count = db.query(UserSession).filter(
            UserSession.session_token_hash == token_hash
        ).delete()
        db.commit()
        return count > 0

    def cleanup_expired_sessions(self, db: Session) -> int:
        """Deletes all expired or timed-out sessions."""
        now = datetime.now(timezone.utc)
        inactivity_cutoff = now - timedelta(minutes=self.inactivity_minutes)

        deleted = db.query(UserSession).filter(
            (UserSession.expires_at <= now) | (UserSession.last_active_at <= inactivity_cutoff)
        ).delete()
        db.commit()
        return deleted


# Global session manager instance
session_manager = SessionManager()
