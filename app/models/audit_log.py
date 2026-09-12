"""
AuditLog ORM model.

Immutable business event log for tracking operations on campaigns, contacts, and messages.
Provides an audit trail for debugging and compliance.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class AuditLog(Base):
    """
    AuditLog model representing business events in the system.

    Columns:
        id: Primary key
        event_type: Type of event (e.g., contact_created, csv_import_complete, validation_error)
        campaign_id: Optional foreign key to campaigns (SET NULL on delete)
        contact_id: Optional foreign key to contacts (SET NULL on delete)
        message_id: Optional foreign key to messages (SET NULL on delete)
        status: Event status/outcome (optional)
        error_message: Human-readable error text if applicable
        retry_count: Retry count associated with the event
        result: Event result or detailed outcome information
        created_at: Event timestamp (UTC)

    Constraints:
        - Foreign keys with SET NULL on delete (audit trail preserved even if records deleted)

    Indexes:
        - idx_audit_logs_event_type (for event type queries)
        - idx_audit_logs_campaign_id (for campaign event history)
        - idx_audit_logs_contact_id (for contact event history)
        - idx_audit_logs_created_at (for time-range queries)
    """

    __tablename__ = "audit_logs"

    __table_args__ = (
        Index("idx_audit_logs_event_type", "event_type"),
        Index("idx_audit_logs_campaign_id", "campaign_id"),
        Index("idx_audit_logs_contact_id", "contact_id"),
        Index("idx_audit_logs_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    status = Column(String)
    error_message = Column(Text)
    retry_count = Column(Integer)
    result = Column(Text)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    campaign = relationship(
        "Campaign",
        back_populates="audit_logs",
        foreign_keys=[campaign_id]
    )
    contact = relationship(
        "Contact",
        back_populates="audit_logs",
        foreign_keys=[contact_id]
    )
    message = relationship(
        "Message",
        back_populates="audit_logs",
        foreign_keys=[message_id]
    )

    @property
    def payload_json(self):
        """Deserializes JSON payload from result text field if present."""
        import json
        if self.result:
            try:
                return json.loads(self.result)
            except Exception:
                pass
        return {}

    @property
    def actor(self):
        """Extracts actor identifier from payload_json if present."""
        return self.payload_json.get("actor")

    def __repr__(self):
        return f"<AuditLog id={self.id} event_type={self.event_type} created_at={self.created_at}>"
