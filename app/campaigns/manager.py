"""
Campaign Manager for CRUD and State Machine.
"""
from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.campaign import Campaign
from app.campaigns.exceptions import (
    CampaignNotFoundError,
    InvalidStateTransitionError,
    CampaignError,
    TemplateValidationError
)
from app.campaigns.template_service import MessageTemplateService
from app.campaigns.audit_logger import CampaignAuditLogger

class CampaignManager:
    """Manages Campaign CRUD, duplication, scheduling, and state transitions."""
    
    VALID_TRANSITIONS = {
        "DRAFT": {"SCHEDULED", "RUNNING"},
        "SCHEDULED": {"RUNNING"},
        "RUNNING": {"PAUSED", "COMPLETED", "CANCELLED", "FAILED"},
        "PAUSED": {"RUNNING", "CANCELLED"},
        "COMPLETED": set(),
        "CANCELLED": set(),
        "FAILED": set(),
    }
    
    def __init__(self, db: Session):
        self.db = db
        self.audit_logger = CampaignAuditLogger(db)

    def create_campaign(self, name: str, message_template: str, **kwargs) -> Campaign:
        """Creates a new campaign in DRAFT state."""
        # Validate template early
        MessageTemplateService.validate_template(message_template)
        
        campaign = Campaign(
            name=name,
            message_template=message_template,
            status="DRAFT",
            daily_limit=kwargs.get("daily_limit", 100),
            min_delay_seconds=kwargs.get("min_delay_seconds", 10),
            max_delay_seconds=kwargs.get("max_delay_seconds", 30),
            batch_size=kwargs.get("batch_size", 20),
            batch_pause_seconds=kwargs.get("batch_pause_seconds", 60)
        )
        self.db.add(campaign)
        try:
            self.db.commit()
            self.db.refresh(campaign)
        except IntegrityError:
            self.db.rollback()
            raise CampaignError(f"Campaign with name '{name}' already exists.")
            
        self.audit_logger.log_event("CAMPAIGN_CREATED", campaign.id, status="DRAFT")
        return campaign

    def get_campaign(self, campaign_id: int) -> Campaign:
        """Retrieves a campaign by ID."""
        campaign = self.db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise CampaignNotFoundError(f"Campaign {campaign_id} not found.")
        return campaign

    def list_campaigns(self, limit: int = 100, offset: int = 0) -> List[Campaign]:
        """Lists campaigns."""
        return self.db.query(Campaign).limit(limit).offset(offset).all()

    def update_campaign(self, campaign_id: int, **kwargs) -> Campaign:
        """Updates campaign fields."""
        campaign = self.get_campaign(campaign_id)
        
        if "message_template" in kwargs:
            MessageTemplateService.validate_template(kwargs["message_template"])
            campaign.message_template = kwargs["message_template"]
            
        updatable_fields = [
            "name", "daily_limit", "min_delay_seconds", "max_delay_seconds", 
            "batch_size", "batch_pause_seconds"
        ]
        
        for field in updatable_fields:
            if field in kwargs:
                setattr(campaign, field, kwargs[field])
                
        try:
            self.db.commit()
            self.db.refresh(campaign)
        except IntegrityError:
            self.db.rollback()
            raise CampaignError(f"Database constraint error during update.")
            
        self.audit_logger.log_event("CAMPAIGN_UPDATED", campaign.id)
        return campaign

    def delete_campaign(self, campaign_id: int) -> None:
        """Deletes a campaign."""
        campaign = self.get_campaign(campaign_id)
        self.db.delete(campaign)
        self.db.commit()
        # Logging deletion is tricky since cascade or ID is gone, but we can log with campaign_id
        self.audit_logger.log_event("CAMPAIGN_DELETED", campaign_id)

    def duplicate_campaign(self, campaign_id: int, new_name: str) -> Campaign:
        """Duplicates an existing campaign."""
        original = self.get_campaign(campaign_id)
        
        new_campaign = Campaign(
            name=new_name,
            message_template=original.message_template,
            status="DRAFT",
            daily_limit=original.daily_limit,
            min_delay_seconds=original.min_delay_seconds,
            max_delay_seconds=original.max_delay_seconds,
            batch_size=original.batch_size,
            batch_pause_seconds=original.batch_pause_seconds
        )
        self.db.add(new_campaign)
        try:
            self.db.commit()
            self.db.refresh(new_campaign)
        except IntegrityError:
            self.db.rollback()
            raise CampaignError(f"Campaign with name '{new_name}' already exists.")
            
        self.audit_logger.log_event("CAMPAIGN_DUPLICATED", new_campaign.id, status="DRAFT", 
                                    result=f"Duplicated from {campaign_id}")
        return new_campaign

    def transition_state(self, campaign_id: int, new_state: str) -> Campaign:
        """Transitions a campaign to a new state if valid."""
        campaign = self.get_campaign(campaign_id)
        current_state = campaign.status
        
        if new_state not in self.VALID_TRANSITIONS.get(current_state, set()):
            raise InvalidStateTransitionError(
                f"Cannot transition campaign from {current_state} to {new_state}."
            )
            
        campaign.status = new_state
        self.db.commit()
        self.db.refresh(campaign)
        
        event_name = f"CAMPAIGN_{new_state}"
        self.audit_logger.log_event(event_name, campaign.id, status=new_state)
        
        return campaign

    def schedule_campaign(self, campaign_id: int, start_at: datetime, end_at: Optional[datetime] = None) -> Campaign:
        """Schedules a campaign."""
        campaign = self.get_campaign(campaign_id)
        campaign.scheduled_start_at = start_at
        campaign.scheduled_end_at = end_at
        
        # Will also try to transition to SCHEDULED
        campaign = self.transition_state(campaign_id, "SCHEDULED")
        return campaign
