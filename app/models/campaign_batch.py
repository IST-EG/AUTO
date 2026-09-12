"""
CampaignBatch ORM model.

Represents a persistent batch of messages in a campaign execution.
Enables reliable resumption if an application crashes or restarts.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    CheckConstraint, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class CampaignBatch(Base):
    """
    CampaignBatch model tracking execution batches persistently.

    Columns:
        id: Primary key
        campaign_id: Foreign key to campaigns (CASCADE delete)
        batch_number: Sequential batch index within the campaign
        status: One of PENDING, IN_PROGRESS, PAUSED, COMPLETED, CANCELLED
        total_items: Total items allocated to this batch
        processed_items: Items processed so far (successful + failed + skipped)
        successful_items: Successfully sent messages
        failed_items: Messages permanently failed
        started_at: When processing for this batch started
        completed_at: When this batch finished
        created_at: Creation timestamp (UTC)
        updated_at: Update timestamp (UTC)
    """

    __tablename__ = "campaign_batches"

    __table_args__ = (
        UniqueConstraint("campaign_id", "batch_number", name="uq_campaign_batch_number"),
        CheckConstraint(
            "status IN ('PENDING','IN_PROGRESS','PAUSED','COMPLETED','CANCELLED')",
            name="ck_batch_status"
        ),
        Index("idx_campaign_batches_campaign_id", "campaign_id"),
        Index("idx_campaign_batches_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    batch_number = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    total_items = Column(Integer, nullable=False, default=0)
    processed_items = Column(Integer, nullable=False, default=0)
    successful_items = Column(Integer, nullable=False, default=0)
    failed_items = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
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
    campaign = relationship("Campaign", back_populates="batches")
    messages = relationship("Message", back_populates="batch")

    def __repr__(self):
        return f"<CampaignBatch id={self.id} campaign_id={self.campaign_id} batch_number={self.batch_number} status={self.status}>"
