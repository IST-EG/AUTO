"""
Contact ORM model.

A contact represents an individual phone number target for outreach campaigns.
Each contact has:
  - Unique (phone_e164, country_code) pair to prevent duplicates
  - Consent status tracking (pending/opted_in/opted_out)
  - Contact status (active/inactive/duplicate) for lifecycle management
  - Duplicate detection via is_duplicate + duplicate_of_id self-reference
  - Message send history and audit trail
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey,
    UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class Contact(Base):
    """
    Contact model representing a phone number to target.

    Columns:
        id: Primary key
        name: Contact name (required)
        phone_e164: E.164 formatted phone number (required, part of unique constraint)
        country_code: ISO country code or numeric dialling prefix (required, part of unique constraint)
        company: Optional company affiliation
        city: Optional city location
        consent_status: One of pending/opted_in/opted_out (default pending)
        opted_in_at: Timestamp when contact was opted in
        opted_out_at: Timestamp when contact was opted out
        last_contacted_at: Last message send timestamp
        messages_sent_count: Running tally of messages sent (default 0)
        contact_status: One of active/inactive/duplicate (default active)
        is_duplicate: Flag indicating this is a duplicate (default False)
        duplicate_of_id: Self-reference to the canonical contact if this is a duplicate
        created_at: Record creation timestamp (UTC)
        updated_at: Record last update timestamp (UTC)

    Constraints:
        - UniqueConstraint on (phone_e164, country_code)
        - CheckConstraint on consent_status enum values
        - CheckConstraint on contact_status enum values

    Indexes:
        - idx_contacts_phone_e164
        - idx_contacts_consent_status
        - idx_contacts_contact_status
        - idx_contacts_country_code
    """

    __tablename__ = "contacts"

    __table_args__ = (
        UniqueConstraint(
            "phone_e164", "country_code",
            name="uq_contact_phone_country"
        ),
        CheckConstraint(
            "consent_status IN ('pending','opted_in','opted_out')",
            name="ck_consent_status"
        ),
        CheckConstraint(
            "contact_status IN ('active','inactive','duplicate')",
            name="ck_contact_status"
        ),
        Index("idx_contacts_phone_e164", "phone_e164"),
        Index("idx_contacts_consent_status", "consent_status"),
        Index("idx_contacts_contact_status", "contact_status"),
        Index("idx_contacts_country_code", "country_code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    phone_e164 = Column(String, nullable=False)
    country_code = Column(String, nullable=False)
    company = Column(String)
    city = Column(String)
    consent_status = Column(String, nullable=False, default="pending")
    opted_in_at = Column(DateTime(timezone=True))
    opted_out_at = Column(DateTime(timezone=True))
    last_contacted_at = Column(DateTime(timezone=True))
    messages_sent_count = Column(Integer, nullable=False, default=0)
    contact_status = Column(String, nullable=False, default="active")
    is_duplicate = Column(Boolean, nullable=False, default=False)
    duplicate_of_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
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
    duplicate_original = relationship(
        "Contact",
        remote_side=[id],
        backref="duplicates",
        foreign_keys=[duplicate_of_id]
    )
    campaign_contacts = relationship(
        "CampaignContact",
        back_populates="contact",
        cascade="all, delete-orphan"
    )
    messages = relationship(
        "Message",
        back_populates="contact",
        cascade="all, delete-orphan"
    )
    unsubscribe = relationship(
        "Unsubscribe",
        back_populates="contact",
        uselist=False,
        cascade="all, delete-orphan"
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="contact",
        foreign_keys="AuditLog.contact_id"
    )

    def __repr__(self):
        return f"<Contact id={self.id} phone={self.phone_e164} name={self.name}>"
