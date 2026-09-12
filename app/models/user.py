"""
User ORM model.

Represents an authorized human operator or viewer with role-based access controls
in the Web Control Center.
"""

from datetime import datetime, timezone
from enum import Enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime
)
from sqlalchemy.orm import relationship

from app.database import Base


class UserRole(str, Enum):
    """Supported roles in Role-Based Access Control (RBAC)."""
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class User(Base):
    """
    User model for Web Control Center authentication and authorization.

    Columns:
        id: Primary key (UUID v4 string)
        username: Unique normalized username
        email: Unique normalized email address
        password_hash: Bcrypt-hashed password string
        role: User privilege level (OWNER, ADMIN, OPERATOR, VIEWER)
        is_active: Account status flag
        last_login_at: Timestamp of most recent successful login (UTC)
        created_at: Account creation timestamp (UTC)
        updated_at: Account modification timestamp (UTC)
    """

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default=UserRole.OPERATOR.value)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username} role={self.role} active={self.is_active}>"
