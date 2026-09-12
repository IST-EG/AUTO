"""
ORM models package.

Re-exports all models so Alembic's autogenerate can discover them via
Base.metadata.

Models are imported in dependency order to ensure all relationships are
properly registered before Alembic scans the metadata.

Usage (e.g., in main.py or Alembic env.py):
    from app.models import *  # Or import individual models
    from app.database import Base
    Base.metadata.create_all(engine)
"""

# Import in dependency order to ensure relationships are registered
from app.models.app_setting import AppSetting
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.campaign_batch import CampaignBatch
from app.models.message import Message
from app.models.unsubscribe import Unsubscribe
from app.models.send_session import SendSession
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.models.template import MessageTemplate, MessageTemplateVersion

__all__ = [
    "AppSetting",
    "AuditLog",
    "Campaign",
    "CampaignBatch",
    "CampaignContact",
    "Contact",
    "Message",
    "MessageTemplate",
    "MessageTemplateVersion",
    "SendSession",
    "Unsubscribe",
    "User",
    "UserRole",
    "UserSession",
]
