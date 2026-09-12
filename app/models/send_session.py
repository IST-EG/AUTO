"""
SendSession ORM model.

Represents a single execution of a campaign, tracking its lifecycle and statistics.
A campaign may have multiple send sessions over time (retries, resumals).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, DateTime, ForeignKey,
    CheckConstraint, Index, String
)
from sqlalchemy.orm import relationship

from app.database import Base


class SendSession(Base):
    """
    SendSession model representing one execution of a campaign.

    Columns:
        id: Primary key
        campaign_id: Foreign key to campaigns (CASCADE delete)
        started_at: Timestamp when the session started (default now)
        ended_at: Timestamp when the session ended
        status: Session state (default active, one of active/completed/error/stopped)
        messages_sent: Count of successfully sent messages (default 0)
        messages_failed: Count of failed messages (default 0)
        created_at: Record creation timestamp (UTC)

    Constraints:
        - CheckConstraint on status enum values
        - Foreign key with CASCADE delete

    Indexes:
        - idx_send_sessions_campaign_id
        - idx_send_sessions_status
    """

    __tablename__ = "send_sessions"

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','error','stopped')",
            name="ck_send_session_status"
        ),
        Index("idx_send_sessions_campaign_id", "campaign_id"),
        Index("idx_send_sessions_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    ended_at = Column(DateTime(timezone=True))
    status = Column(String, nullable=False, default="active")
    messages_sent = Column(Integer, nullable=False, default=0)
    messages_failed = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    campaign = relationship("Campaign", back_populates="send_sessions")

    def __repr__(self):
        return f"<SendSession id={self.id} campaign_id={self.campaign_id} status={self.status}>"
