"""
Web service for Campaign-Contact membership and eligibility.
"""

from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.campaigns.contact_manager import CampaignContactManager
from app.campaigns.exceptions import CampaignContactError
from app.web.schemas.campaigns import (
    CampaignContactDTO,
    CampaignAddContactsResponse,
)


class CampaignContactService:
    """Service mediating campaign contact membership and eligibility checks."""

    @staticmethod
    def list_campaign_contacts(
        db: Session,
        campaign_id: int,
        status_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[CampaignContactDTO]:
        """Lists contacts linked to a campaign with their current execution status."""
        campaign = db.query(Campaign.id).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found."
            )

        query = (
            db.query(CampaignContact)
            .filter(CampaignContact.campaign_id == campaign_id)
        )
        if status_filter:
            query = query.filter(CampaignContact.status == status_filter.upper())

        cc_records = query.order_by(CampaignContact.id.asc()).offset(offset).limit(limit).all()

        results = []
        for cc in cc_records:
            contact = cc.contact
            results.append(
                CampaignContactDTO(
                    id=cc.id,
                    campaign_id=cc.campaign_id,
                    contact_id=cc.contact_id,
                    contact_name=contact.name if contact else "Unknown",
                    contact_phone=contact.phone_e164 if contact else "Unknown",
                    contact_company=contact.company if contact else None,
                    contact_city=contact.city if contact else None,
                    status=cc.status,
                    exclusion_reason=cc.exclusion_reason,
                    enqueued_at=cc.enqueued_at,
                    sent_at=cc.sent_at,
                )
            )
        return results

    @staticmethod
    def add_contacts(
        db: Session,
        campaign_id: int,
        contact_ids: Optional[List[int]] = None,
        phone_e164: Optional[str] = None,
        name: Optional[str] = None,
        company: Optional[str] = None,
        username: Optional[str] = None
    ) -> CampaignAddContactsResponse:
        """Adds contacts to a campaign evaluating eligibility."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found."
            )

        resolved_contacts = []
        if contact_ids:
            resolved_contacts = db.query(Contact).filter(Contact.id.in_(contact_ids)).all()

        if phone_e164:
            clean_phone = phone_e164.strip()
            # Find or create contact
            c = db.query(Contact).filter(Contact.phone_e164 == clean_phone).first()
            if not c:
                c = Contact(
                    name=name.strip() if name and name.strip() else clean_phone,
                    phone_e164=clean_phone,
                    company=company.strip() if company and company.strip() else None,
                    contact_status="active",
                    consent_status="opted_in"
                )
                db.add(c)
                db.commit()
                db.refresh(c)
            if c not in resolved_contacts:
                resolved_contacts.append(c)

        if not resolved_contacts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No matching contacts found for the given IDs or phone number."
            )

        mgr = CampaignContactManager(db)
        summary = mgr.add_multiple_contacts(campaign, resolved_contacts)

        # If campaign is RUNNING, auto-enqueue new ELIGIBLE contacts immediately
        if campaign.status == "RUNNING":
            from app.web.services.runner_control_service import RunnerControlService
            RunnerControlService.enqueue_eligible_contacts(db, campaign)

        return CampaignAddContactsResponse(
            total=summary.get("total", 0),
            added=summary.get("added", 0),
            excluded=summary.get("excluded", 0),
            duplicates=summary.get("duplicates", 0),
        )

    @staticmethod
    def remove_contact(
        db: Session,
        campaign_id: int,
        contact_id: int,
        username: Optional[str] = None
    ) -> bool:
        """
        Removes a contact from a campaign.
        Enforces strict safety rule: removals are only permitted in DRAFT state.
        """
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found."
            )

        if campaign.status not in ("DRAFT", "PAUSED"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot remove contacts from campaign in '{campaign.status}' state. Only DRAFT campaigns allow removals."
            )

        mgr = CampaignContactManager(db)
        try:
            removed = mgr.remove_contact(campaign_id, contact_id)
            if not removed:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Contact {contact_id} not found in Campaign {campaign_id}."
                )
            return True
        except CampaignContactError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc)
            )
