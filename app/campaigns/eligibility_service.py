"""
Campaign Eligibility Service.
"""
from dataclasses import dataclass
from sqlalchemy.orm import Session
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact

@dataclass
class EligibilityResult:
    eligible: bool
    reason: str
    contact_id: int
    campaign_id: int


class CampaignEligibilityService:
    """Service to determine if a contact is eligible for a campaign."""
    
    def __init__(self, db: Session):
        self.db = db
        
    def check_eligibility(self, contact: Contact, campaign: Campaign) -> EligibilityResult:
        """
        Determines whether a contact can participate in a campaign.
        """
        # 1. Invalid phone checks (assuming they wouldn't be stored if invalid,
        # but contact_status could be inactive)
        if contact.contact_status == "inactive":
            return EligibilityResult(False, "INVALID_PHONE", contact.id, campaign.id)
            
        # 2. Consent checks
        if contact.consent_status == "opted_out":
            return EligibilityResult(False, "OPTED_OUT", contact.id, campaign.id)
            
        # If we require explicit opt_in, we'd check it here. 
        # But for now, pending or opted_in is usually fine.
        # However, let's say we only send to pending/opted_in. 
        # If there's a strict NO_CONSENT rule later, we can add it.
        # For now, let's treat "pending" or "opted_in" as valid.
        
        # 3. Duplicate contact status
        if contact.contact_status == "duplicate" or contact.is_duplicate:
            return EligibilityResult(False, "DUPLICATE", contact.id, campaign.id)
            
        # 4. Already contacted in this campaign
        existing_campaign_contact = self.db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.contact_id == contact.id
        ).first()
        
        if existing_campaign_contact and existing_campaign_contact.status in ("SENT", "QUEUED", "PENDING"):
            return EligibilityResult(False, "ALREADY_CONTACTED", contact.id, campaign.id)
            
        # 5. Frequency limit
        # For Phase 2, we can implement a basic check or just pass. 
        # (E.g., contact.last_contacted_at)
        # If there's a global daily limit per contact, we'd check it here.
        # We'll assume eligible by default if none of the above hit.

        return EligibilityResult(True, "ELIGIBLE", contact.id, campaign.id)
