"""
CampaignContact ORM model.

A join table linking campaigns to contacts, tracking the enqueue and send status
of each contact within a campaign. Prevents duplicate enqueuing via unique constraint.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class CampaignContact(Base):
    """
    CampaignContact model representing a contact's participation in a campaign.

    Columns:
        id: Primary key
        campaign_id: Foreign key to campaigns (CASCADE delete)
        contact_id: Foreign key to contacts (CASCADE delete)
        status: One of pending/sent/failed/skipped (default pending)
        enqueued_at: Timestamp when this contact was added to the campaign (default now)
        sent_at: Timestamp when the message was sent
        skip_reason: Human-readable reason if skipped
        created_at: Record creation timestamp (UTC)

    Constraints:
        - UniqueConstraint on (campaign_id, contact_id) to prevent duplicate enqueuing
        - CheckConstraint on status enum values
        - Foreign keys with CASCADE delete

    Indexes:
        - idx_cc_campaign_id
        - idx_cc_contact_id
        - idx_cc_status
    """

    __tablename__ = "campaign_contacts"

    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "contact_id",
            name="uq_campaign_contact"
        ),
        CheckConstraint(
            "status IN ('PENDING','ELIGIBLE','EXCLUDED','QUEUED','SENT','FAILED','SKIPPED')",
            name="ck_cc_status"
        ),
        Index("idx_cc_campaign_id", "campaign_id"),
        Index("idx_cc_contact_id", "contact_id"),
        Index("idx_cc_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    exclusion_reason = Column(String, nullable=True)
    enqueued_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    sent_at = Column(DateTime(timezone=True))
    skip_reason = Column(String)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    campaign = relationship("Campaign", back_populates="campaign_contacts")
    contact = relationship("Contact", back_populates="campaign_contacts")
    messages = relationship(
        "Message",
        back_populates="campaign_contact",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<CampaignContact campaign_id={self.campaign_id} contact_id={self.contact_id} status={self.status}>"
