"""
Message ORM model.

Represents an individual message attempt to a contact as part of a campaign.
Tracks rendering, sending status, retries, worker leases, and errors.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    CheckConstraint, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class Message(Base):
    """
    Message model representing a queued or sent message in a campaign.

    Columns:
        id: Primary key
        campaign_id: Foreign key to campaigns (CASCADE delete)
        contact_id: Foreign key to contacts (CASCADE delete)
        campaign_contact_id: Optional foreign key to campaign_contacts (CASCADE delete)
        batch_id: Optional foreign key to campaign_batches (SET NULL on delete)
        sequence_number: Message attempt / sequence index within campaign contact (default 1)
        idempotency_key: Unique deterministic idempotency identifier (required)
        rendered_content: Final message content after template rendering (required)
        status: One of PENDING, QUEUED, PROCESSING, SENT, FAILED, RETRY_PENDING, CANCELLED, SKIPPED
        attempt_count: Number of delivery attempts made (default 0)
        max_attempts: Maximum allowed delivery attempts (default 3)
        retry_count: Alias for backwards compatibility with earlier schemas
        last_attempt_at: Timestamp of the most recent delivery attempt
        next_retry_at: Earliest timestamp when this message may be retried
        error_type: Classification of failure (TEMPORARY or PERMANENT)
        locked_at: Timestamp when worker lease was acquired
        locked_by: Worker identifier holding the lease
        last_error: Most recent error message or exception text
        queued_at: Timestamp when message was created / queued
        sent_at: Timestamp when message was successfully sent
        failed_at: Timestamp when message failed permanently
        created_at: Record creation timestamp (UTC)
        updated_at: Record last update timestamp (UTC)

    Constraints:
        - UniqueConstraint on idempotency_key
        - CheckConstraint on status enum values
        - Foreign keys with appropriate CASCADE/SET NULL

    Indexes:
        - idx_messages_campaign_id
        - idx_messages_contact_id
        - idx_messages_status
        - idx_messages_next_retry_at
        - idx_messages_idempotency_key
    """

    __tablename__ = "messages"

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_messages_idempotency_key"),
        CheckConstraint(
            "status IN ('PENDING','QUEUED','PROCESSING','SENT','FAILED','RETRY_PENDING','CANCELLED','SKIPPED')",
            name="ck_message_status"
        ),
        Index("idx_messages_campaign_id", "campaign_id"),
        Index("idx_messages_contact_id", "contact_id"),
        Index("idx_messages_status", "status"),
        Index("idx_messages_next_retry_at", "next_retry_at"),
        Index("idx_messages_idempotency_key", "idempotency_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    campaign_contact_id = Column(Integer, ForeignKey("campaign_contacts.id", ondelete="CASCADE"), nullable=True)
    batch_id = Column(Integer, ForeignKey("campaign_batches.id", ondelete="SET NULL"), nullable=True)
    sequence_number = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String, nullable=False, unique=True)
    rendered_content = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    retry_count = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    error_type = Column(String, nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    queued_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
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
    campaign = relationship("Campaign", back_populates="messages")
    contact = relationship("Contact", back_populates="messages")
    campaign_contact = relationship("CampaignContact", back_populates="messages")
    batch = relationship("CampaignBatch", back_populates="messages")
    audit_logs = relationship(
        "AuditLog",
        back_populates="message",
        foreign_keys="AuditLog.message_id"
    )

    def __repr__(self):
        return f"<Message id={self.id} campaign_id={self.campaign_id} contact_id={self.contact_id} status={self.status}>"
