"""
Campaign ORM model.

A campaign represents a single outreach operation targeting a set of contacts
with a template message. Campaigns have execution parameters (delays, batch sizes,
retry logic) and lifecycle states (draft → running → paused/stopped → completed).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint,
    CheckConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class Campaign(Base):
    """
    Campaign model representing an outreach operation.

    Columns:
        id: Primary key
        name: Unique campaign identifier (required)
        message_template: Template content for messages (required)
        daily_limit: Max messages per calendar day (default 100, must be >= 1)
        min_delay_seconds: Minimum delay between messages in seconds (default 10, must be >= 1)
        max_delay_seconds: Maximum delay between messages in seconds (default 30, must be >= 1)
        batch_size: Messages to send before pausing (default 20, must be >= 1)
        batch_pause_seconds: Seconds to pause after each batch (default 60, must be >= 0)
        status: Campaign state (default draft, one of draft/running/paused/stopped/completed)
        queue_position: Position in the global send queue (default 0)
        scheduled_start_at: Optional scheduled start time (UTC)
        scheduled_end_at: Optional scheduled end time (UTC)
        timezone: IANA timezone for date boundary calculation (default Africa/Cairo)
        max_retries: Max retry attempts for failed messages (default 3, 0-10)
        error_threshold: Errors before pause (default 5, must be >= 1)
        element_wait_timeout: Seconds to wait for elements (default 30, 5-60)
        created_at: Record creation timestamp (UTC)
        updated_at: Record last update timestamp (UTC)

    Constraints:
        - UniqueConstraint on name
        - CheckConstraint on daily_limit >= 1
        - CheckConstraint on min_delay_seconds >= 1
        - CheckConstraint on max_delay_seconds >= 1
        - CheckConstraint on batch_size >= 1
        - CheckConstraint on batch_pause_seconds >= 0
        - CheckConstraint on status enum values
        - CheckConstraint on max_retries BETWEEN 0 AND 10
        - CheckConstraint on error_threshold >= 1
        - CheckConstraint on element_wait_timeout BETWEEN 5 AND 60

    Indexes:
        - idx_campaigns_status
    """

    __tablename__ = "campaigns"

    __table_args__ = (
        UniqueConstraint("name", name="uq_campaign_name"),
        CheckConstraint("daily_limit >= 1", name="ck_daily_limit_min"),
        CheckConstraint("min_delay_seconds >= 1", name="ck_min_delay_min"),
        CheckConstraint("max_delay_seconds >= 1", name="ck_max_delay_min"),
        CheckConstraint("batch_size >= 1", name="ck_batch_size_min"),
        CheckConstraint("batch_pause_seconds >= 0", name="ck_batch_pause_min"),
        CheckConstraint(
            "status IN ('DRAFT','SCHEDULED','RUNNING','PAUSED','COMPLETED','CANCELLED','FAILED')",
            name="ck_campaign_status"
        ),
        CheckConstraint("max_retries BETWEEN 0 AND 10", name="ck_max_retries_range"),
        CheckConstraint("error_threshold >= 1", name="ck_error_threshold_min"),
        CheckConstraint(
            "element_wait_timeout BETWEEN 5 AND 60",
            name="ck_element_wait_timeout_range"
        ),
        Index("idx_campaigns_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    message_template = Column(Text, nullable=False)
    daily_limit = Column(Integer, nullable=False, default=100)
    min_delay_seconds = Column(Integer, nullable=False, default=10)
    max_delay_seconds = Column(Integer, nullable=False, default=30)
    batch_size = Column(Integer, nullable=False, default=20)
    batch_pause_seconds = Column(Integer, nullable=False, default=60)
    status = Column(String, nullable=False, default="DRAFT")
    queue_position = Column(Integer, nullable=False, default=0)
    scheduled_start_at = Column(DateTime(timezone=True))
    scheduled_end_at = Column(DateTime(timezone=True))
    timezone = Column(String, nullable=False, default="Africa/Cairo")
    max_retries = Column(Integer, nullable=False, default=3)
    error_threshold = Column(Integer, nullable=False, default=5)
    element_wait_timeout = Column(Integer, nullable=False, default=30)
    template_version_id = Column(
        Integer,
        ForeignKey("message_template_versions.id", ondelete="SET NULL"),
        nullable=True
    )
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
    template_version = relationship(
        "MessageTemplateVersion",
        foreign_keys=[template_version_id]
    )
    campaign_contacts = relationship(
        "CampaignContact",
        back_populates="campaign",
        cascade="all, delete-orphan"
    )
    messages = relationship(
        "Message",
        back_populates="campaign",
        cascade="all, delete-orphan"
    )
    batches = relationship(
        "CampaignBatch",
        back_populates="campaign",
        cascade="all, delete-orphan"
    )
    send_sessions = relationship(
        "SendSession",
        back_populates="campaign",
        cascade="all, delete-orphan"
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="campaign",
        foreign_keys="AuditLog.campaign_id"
    )

    def __repr__(self):
        return f"<Campaign id={self.id} name={self.name} status={self.status}>"
