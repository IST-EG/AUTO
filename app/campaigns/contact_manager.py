"""
Campaign Contact Manager for assigning contacts to campaigns.
"""
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.campaigns.exceptions import CampaignContactError
from app.campaigns.eligibility_service import CampaignEligibilityService
from app.campaigns.audit_logger import CampaignAuditLogger

class CampaignContactManager:
    """Manages adding and removing contacts from campaigns."""
    
    def __init__(self, db: Session):
        self.db = db
        self.eligibility_service = CampaignEligibilityService(db)
        self.audit_logger = CampaignAuditLogger(db)

    def add_contact(self, campaign: Campaign, contact: Contact) -> CampaignContact:
        """
        Adds a single contact to a campaign after checking eligibility.
        """
        eligibility = self.eligibility_service.check_eligibility(contact, campaign)
        
        status = "ELIGIBLE" if eligibility.eligible else "EXCLUDED"
        exclusion_reason = None if eligibility.eligible else eligibility.reason
        
        campaign_id = campaign.id
        contact_id = contact.id
        
        campaign_contact = CampaignContact(
            campaign_id=campaign_id,
            contact_id=contact_id,
            status=status,
            exclusion_reason=exclusion_reason
        )
        
        self.db.add(campaign_contact)
        try:
            self.db.commit()
            self.db.refresh(campaign_contact)
        except IntegrityError:
            self.db.rollback()
            raise CampaignContactError(f"Contact {contact_id} is already in Campaign {campaign_id}.")
            
        # Log the addition
        event_type = "CONTACT_ADDED" if eligibility.eligible else "CONTACT_EXCLUDED"
        self.audit_logger.log_event(
            event_type, 
            campaign_id, 
            contact_id=contact_id, 
            status=status,
            result=exclusion_reason
        )
            
        return campaign_contact

    def add_multiple_contacts(self, campaign: Campaign, contacts: List[Contact]) -> dict:
        """
        Adds multiple contacts to a campaign. Returns summary of added/excluded.
        """
        results = {
            "total": len(contacts),
            "added": 0,
            "excluded": 0,
            "duplicates": 0
        }
        
        for contact in contacts:
            try:
                cc = self.add_contact(campaign, contact)
                if cc.status == "EXCLUDED":
                    results["excluded"] += 1
                else:
                    results["added"] += 1
            except CampaignContactError:
                results["duplicates"] += 1
                
        return results

    def remove_contact(self, campaign_id: int, contact_id: int) -> bool:
        """
        Removes a contact from a campaign if it hasn't been sent yet.
        """
        cc = self.db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.contact_id == contact_id
        ).first()
        
        if not cc:
            return False
            
        if cc.status in ("SENT", "FAILED"):
            raise CampaignContactError("Cannot remove contact that has already been processed.")
            
        self.db.delete(cc)
        self.db.commit()
        
        self.audit_logger.log_event(
            "CONTACT_REMOVED", 
            campaign_id, 
            contact_id=contact_id
        )
        return True
