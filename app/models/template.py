"""
Message Template ORM models.

Defines reusable message templates with strictly immutable versioning.
Templates have a logical container (MessageTemplate) and an append-only
history of versions (MessageTemplateVersion).
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    UniqueConstraint, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class MessageTemplate(Base):
    """
    Message Template container model.
    
    Represents a reusable template entity. Template bodies are stored
    in immutable MessageTemplateVersion records.
    """
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
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
    versions = relationship(
        "MessageTemplateVersion",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="MessageTemplateVersion.version_number"
    )

    def __repr__(self):
        return f"<MessageTemplate id={self.id} name={self.name}>"


class MessageTemplateVersion(Base):
    """
    Immutable version of a message template.
    
    Once created, a version must never be modified. Version numbers are
    monotonically increasing (1, 2, 3...) per template.
    """
    __tablename__ = "message_template_versions"

    __table_args__ = (
        UniqueConstraint("template_id", "version_number", name="uq_template_version"),
        Index("idx_template_version_lookup", "template_id", "version_number"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(
        Integer,
        ForeignKey("message_templates.id", ondelete="CASCADE"),
        nullable=False
    )
    version_number = Column(Integer, nullable=False)
    body = Column(Text, nullable=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    template = relationship("MessageTemplate", back_populates="versions")

    def __repr__(self):
        return f"<MessageTemplateVersion id={self.id} template_id={self.template_id} v={self.version_number}>"
