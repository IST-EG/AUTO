"""
AppSetting ORM model.

Key-value configuration store for application-wide settings that may change
at runtime without code deployment.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, UniqueConstraint
)

from app.database import Base


class AppSetting(Base):
    """
    AppSetting model representing a configuration key-value pair.

    Columns:
        id: Primary key
        key: Unique setting identifier (required)
        value: Setting value as text (required)
        description: Human-readable description of the setting
        updated_at: Timestamp of last update (UTC)

    Constraints:
        - UniqueConstraint on key to enforce one value per setting
    """

    __tablename__ = "app_settings"

    __table_args__ = (
        UniqueConstraint("key", name="uq_setting_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, nullable=False, unique=True)
    value = Column(Text, nullable=False)
    description = Column(Text)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f"<AppSetting key={self.key} value={self.value}>"
