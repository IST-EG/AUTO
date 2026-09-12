"""
Campaigns module for campaign management, contact assignment, and rendering.
"""

from app.campaigns.manager import CampaignManager
from app.campaigns.contact_manager import CampaignContactManager
from app.campaigns.template_service import MessageTemplateService
from app.campaigns.eligibility_service import CampaignEligibilityService
from app.campaigns.statistics_service import CampaignStatisticsService
from app.campaigns.audit_logger import CampaignAuditLogger

from app.campaigns.exceptions import (
    CampaignError,
    InvalidStateTransitionError,
    TemplateValidationError,
    CampaignNotFoundError,
    CampaignContactError
)

__all__ = [
    "CampaignManager",
    "CampaignContactManager",
    "MessageTemplateService",
    "CampaignEligibilityService",
    "CampaignStatisticsService",
    "CampaignAuditLogger",
    "CampaignError",
    "InvalidStateTransitionError",
    "TemplateValidationError",
    "CampaignNotFoundError",
    "CampaignContactError"
]
