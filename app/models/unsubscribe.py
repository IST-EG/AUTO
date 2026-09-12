"""
Unsubscribe ORM model.

Represents a contact's opt-out request from communications.
One-to-one relationship with Contact (one contact can only opt-out once).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Boolean, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class Unsubscribe(Base):
    """
    Unsubscribe model representing a contact's opt-out.

    Columns:
        id: Primary key
        contact_id: Foreign key to contacts (CASCADE delete, UNIQUE to enforce one opt-out per contact)
        keyword_matched: The unsubscribe keyword/phrase that triggered the opt-out
        opted_out_at: Timestamp of the opt-out (default now)
        confirmation_sent: Whether confirmation of opt-out was sent (default False)
        created_at: Record creation timestamp (UTC)

    Constraints:
        - UniqueConstraint on contact_id (one opt-out per contact)
        - Foreign key with CASCADE delete

    Indexes:
        - idx_unsubscribes_contact_id
    """

    __tablename__ = "unsubscribes"

    __table_args__ = (
        UniqueConstraint("contact_id", name="uq_unsubscribe_contact"),
        Index("idx_unsubscribes_contact_id", "contact_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    keyword_matched = Column(String)
    opted_out_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    confirmation_sent = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    contact = relationship("Contact", back_populates="unsubscribe")

    def __repr__(self):
        return f"<Unsubscribe id={self.id} contact_id={self.contact_id}>"
