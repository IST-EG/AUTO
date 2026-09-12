"""
UserSession ORM model.

Stores server-side session tokens in hashed form for authenticated Web Control Center
operators.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class UserSession(Base):
    """
    UserSession model for managing stateful server-side sessions.

    Security Invariant:
        Only cryptographic hashes (SHA-256) of raw session tokens are stored.
        Raw tokens are never persisted in the database.

    Columns:
        id: Primary key (UUID v4 string)
        user_id: Foreign key referencing users.id
        session_token_hash: SHA-256 hex digest of the raw session token
        ip_address: Client IP address at session creation/activity
        user_agent: Client User-Agent header (truncated to 255 chars)
        expires_at: Absolute expiration timestamp (UTC)
        created_at: Session creation timestamp (UTC)
        last_active_at: Most recent user activity timestamp (UTC) for sliding inactivity timeout
    """

    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token_hash = Column(String(64), nullable=False, unique=True, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    last_active_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    user = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        return f"<UserSession id={self.id} user_id={self.user_id} expires_at={self.expires_at}>"
