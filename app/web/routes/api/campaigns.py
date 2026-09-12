"""
API Router for Campaigns.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.orm import Session

from app.models.user import User
from app.web.dependencies import (
    get_db,
    get_current_user,
    require_operator,
    verify_csrf,
)
from app.web.schemas.common import APIResponse
from app.web.schemas.campaigns import (
    CampaignListDTO,
    CampaignDetailDTO,
    CampaignCreateRequest,
    CampaignUpdateRequest,
    CampaignStatusTransitionRequest,
    CampaignContactDTO,
    CampaignAddContactsRequest,
    CampaignAddContactsResponse,
)
from app.web.services.campaign_service import CampaignService
from app.web.services.campaign_contact_service import CampaignContactService

router = APIRouter(prefix="/api/v1/campaigns", tags=["Campaigns"])


@router.get("", response_model=APIResponse[List[CampaignListDTO]])
def list_campaigns(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists campaigns with search and status filtering."""
    data = CampaignService.list_campaigns(
        db, status_filter=status, search=search, limit=limit, offset=offset
    )
    return APIResponse.ok(data)


@router.post(
    "",
    response_model=APIResponse[CampaignDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def create_campaign(
    req: CampaignCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates a new campaign in DRAFT state."""
    data = CampaignService.create_campaign(db, req, username=current_user.username)
    return APIResponse.ok(data)


@router.get("/{campaign_id}", response_model=APIResponse[CampaignDetailDTO])
def get_campaign(
    campaign_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves full campaign details including contact statistics breakdown."""
    data = CampaignService.get_campaign(db, campaign_id)
    return APIResponse.ok(data)


@router.patch(
    "/{campaign_id}",
    response_model=APIResponse[CampaignDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def update_campaign(
    campaign_id: int = Path(..., ge=1),
    req: CampaignUpdateRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Updates campaign parameters. Restricted strictly to campaigns in DRAFT state."""
    data = CampaignService.update_campaign(
        db, campaign_id, req, username=current_user.username
    )
    return APIResponse.ok(data)


@router.post(
    "/{campaign_id}/status",
    response_model=APIResponse[CampaignDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def transition_campaign_status(
    campaign_id: int = Path(..., ge=1),
    req: CampaignStatusTransitionRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Transitions campaign lifecycle state.
    Transition to RUNNING is strictly a domain state transition; it does not launch a runner.
    """
    data = CampaignService.transition_status(
        db, campaign_id, req.status, username=current_user.username
    )
    return APIResponse.ok(data)


@router.get("/{campaign_id}/contacts", response_model=APIResponse[List[CampaignContactDTO]])
def list_campaign_contacts(
    campaign_id: int = Path(..., ge=1),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists contacts participating in a campaign with their current state."""
    data = CampaignContactService.list_campaign_contacts(
        db, campaign_id=campaign_id, status_filter=status, limit=limit, offset=offset
    )
    return APIResponse.ok(data)


@router.post(
    "/{campaign_id}/contacts",
    response_model=APIResponse[CampaignAddContactsResponse],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def add_campaign_contacts(
    campaign_id: int = Path(..., ge=1),
    req: CampaignAddContactsRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Adds contacts to a campaign evaluating eligibility."""
    data = CampaignContactService.add_contacts(
        db, campaign_id=campaign_id, contact_ids=req.contact_ids, username=current_user.username
    )
    return APIResponse.ok(data)


@router.delete(
    "/{campaign_id}/contacts/{contact_id}",
    response_model=APIResponse[bool],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def remove_campaign_contact(
    campaign_id: int = Path(..., ge=1),
    contact_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Removes a contact from a campaign. Allowed only when campaign is in DRAFT state."""
    data = CampaignContactService.remove_contact(
        db, campaign_id=campaign_id, contact_id=contact_id, username=current_user.username
    )
    return APIResponse.ok(data)
