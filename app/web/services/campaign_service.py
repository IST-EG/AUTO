"""
Web service for Campaign Management.
"""

from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.template import MessageTemplate, MessageTemplateVersion
from app.campaigns.manager import CampaignManager
from app.campaigns.statistics_service import CampaignStatisticsService
from app.campaigns.exceptions import (
    CampaignNotFoundError,
    InvalidStateTransitionError,
    CampaignError,
    TemplateValidationError,
)
from app.web.schemas.campaigns import (
    CampaignCreateRequest,
    CampaignUpdateRequest,
    CampaignListDTO,
    CampaignDetailDTO,
    CampaignStatsDTO,
)


class CampaignService:
    """Service mediating web-based campaign operations with authoritative domain services."""

    @staticmethod
    def list_campaigns(
        db: Session,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[CampaignListDTO]:
        """Lists campaigns with optional status filtering and search."""
        query = db.query(Campaign)
        if status_filter:
            query = query.filter(Campaign.status == status_filter.upper())
        if search and search.strip():
            query = query.filter(Campaign.name.ilike(f"%{search.strip()}%"))

        campaigns = query.order_by(Campaign.id.desc()).offset(offset).limit(limit).all()

        results = []
        for c in campaigns:
            total_contacts = (
                db.query(func.count(CampaignContact.id))
                .filter(CampaignContact.campaign_id == c.id)
                .scalar() or 0
            )
            results.append(
                CampaignListDTO(
                    id=c.id,
                    name=c.name,
                    status=c.status,
                    daily_limit=c.daily_limit,
                    min_delay_seconds=c.min_delay_seconds,
                    max_delay_seconds=c.max_delay_seconds,
                    batch_size=c.batch_size,
                    batch_pause_seconds=c.batch_pause_seconds,
                    template_version_id=c.template_version_id,
                    total_contacts=total_contacts,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
            )
        return results

    @staticmethod
    def get_campaign(db: Session, campaign_id: int) -> CampaignDetailDTO:
        """Retrieves full campaign details including contact statistics breakdown."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found."
            )

        raw_stats = CampaignStatisticsService(db).get_statistics(campaign_id)
        stats = CampaignStatsDTO(
            total_contacts=raw_stats.get("total_contacts", 0),
            pending=raw_stats.get("pending", 0),
            eligible=raw_stats.get("eligible", 0),
            excluded=raw_stats.get("excluded", 0),
            queued=raw_stats.get("queued", 0),
            sent=raw_stats.get("sent", 0),
            failed=raw_stats.get("failed", 0),
            skipped=raw_stats.get("skipped", 0),
            completion_percentage=raw_stats.get("completion_percentage", 0.0),
        )

        return CampaignDetailDTO(
            id=campaign.id,
            name=campaign.name,
            status=campaign.status,
            message_template=campaign.message_template,
            template_version_id=campaign.template_version_id,
            daily_limit=campaign.daily_limit,
            min_delay_seconds=campaign.min_delay_seconds,
            max_delay_seconds=campaign.max_delay_seconds,
            batch_size=campaign.batch_size,
            batch_pause_seconds=campaign.batch_pause_seconds,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
            stats=stats,
        )

    @staticmethod
    def create_campaign(db: Session, req: CampaignCreateRequest, username: Optional[str] = None) -> CampaignDetailDTO:
        """Creates a campaign, snapshotting template content for immutability."""
        template_text: Optional[str] = None
        template_version_id: Optional[int] = None

        if req.template_version_id:
            ver = db.query(MessageTemplateVersion).filter(
                MessageTemplateVersion.id == req.template_version_id
            ).first()
            if not ver:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Template version {req.template_version_id} not found."
                )
            template_text = ver.body
            template_version_id = ver.id
        elif req.template_id:
            tmpl = db.query(MessageTemplate).filter(
                MessageTemplate.id == req.template_id
            ).first()
            if not tmpl or not tmpl.versions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Template {req.template_id} not found or contains no versions."
                )
            latest_ver = tmpl.versions[-1]
            template_text = latest_ver.body
            template_version_id = latest_ver.id
        elif req.message_template:
            template_text = req.message_template
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either message_template or template_id/template_version_id must be provided."
            )

        mgr = CampaignManager(db)
        try:
            campaign = mgr.create_campaign(
                name=req.name.strip(),
                message_template=template_text,
                daily_limit=req.daily_limit,
                min_delay_seconds=req.min_delay_seconds,
                max_delay_seconds=req.max_delay_seconds,
                batch_size=req.batch_size,
                batch_pause_seconds=req.batch_pause_seconds,
            )
            if template_version_id:
                campaign.template_version_id = template_version_id
                db.commit()
                db.refresh(campaign)
        except TemplateValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc)
            )
        except CampaignError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc)
            )

        return CampaignService.get_campaign(db, campaign.id)

    @staticmethod
    def update_campaign(
        db: Session,
        campaign_id: int,
        req: CampaignUpdateRequest,
        username: Optional[str] = None
    ) -> CampaignDetailDTO:
        """Updates an existing campaign. Only DRAFT campaigns can be modified."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found."
            )

        if campaign.status != "DRAFT":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot edit campaign in '{campaign.status}' state. Only DRAFT campaigns can be modified."
            )

        kwargs = {}
        if req.name is not None:
            kwargs["name"] = req.name.strip()
        if req.daily_limit is not None:
            kwargs["daily_limit"] = req.daily_limit
        if req.min_delay_seconds is not None:
            kwargs["min_delay_seconds"] = req.min_delay_seconds
        if req.max_delay_seconds is not None:
            kwargs["max_delay_seconds"] = req.max_delay_seconds
        if req.batch_size is not None:
            kwargs["batch_size"] = req.batch_size
        if req.batch_pause_seconds is not None:
            kwargs["batch_pause_seconds"] = req.batch_pause_seconds

        if req.template_version_id is not None:
            ver = db.query(MessageTemplateVersion).filter(
                MessageTemplateVersion.id == req.template_version_id
            ).first()
            if not ver:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Template version {req.template_version_id} not found."
                )
            kwargs["message_template"] = ver.body
            campaign.template_version_id = ver.id
        elif req.message_template is not None:
            kwargs["message_template"] = req.message_template
            campaign.template_version_id = None

        mgr = CampaignManager(db)
        try:
            mgr.update_campaign(campaign_id, **kwargs)
        except TemplateValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc)
            )
        except CampaignError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc)
            )

        return CampaignService.get_campaign(db, campaign_id)

    @staticmethod
    def transition_status(
        db: Session,
        campaign_id: int,
        new_status: str,
        username: Optional[str] = None
    ) -> CampaignDetailDTO:
        """
        Transitions campaign lifecycle state according to domain state machine.
        Setting to RUNNING is strictly a domain state transition; it does NOT launch a runner.
        """
        mgr = CampaignManager(db)
        try:
            mgr.transition_state(campaign_id, new_status.upper())
        except CampaignNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign {campaign_id} not found."
            )
        except InvalidStateTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc)
            )

        return CampaignService.get_campaign(db, campaign_id)
