"""
API Router for Queue & Message Operations.
"""

from datetime import datetime
from typing import Optional
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
from app.web.schemas.queue import (
    QueueListResponse,
    QueueStatsDTO,
    QueueMessageDetailDTO,
    QueueReconcileResponse,
    UnknownOutcomeResolveRequest,
)
from app.web.services.queue_service import WebQueueService

router = APIRouter(prefix="/api/v1/queue", tags=["Queue"])


@router.get("", response_model=APIResponse[QueueListResponse])
def list_queue_messages(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    campaign_id: Optional[int] = Query(None, description="Filter by campaign ID"),
    status: Optional[str] = Query(None, description="Filter by message status or UNKNOWN_OUTCOME"),
    contact_id: Optional[int] = Query(None, description="Filter by contact ID"),
    contact_query: Optional[str] = Query(None, description="Search contact name or phone"),
    retry_filter: Optional[str] = Query(None, description="Filter by retry status: HAS_RETRY, NO_RETRY, RETRY_PENDING"),
    date_from: Optional[datetime] = Query(None, description="Filter messages created on or after date"),
    date_to: Optional[datetime] = Query(None, description="Filter messages created on or before date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists queued and historical message records with server-side pagination,
    filtering, and role-based phone privacy masking.
    """
    data = WebQueueService.list_messages(
        db=db,
        user=current_user,
        page=page,
        page_size=page_size,
        campaign_id=campaign_id,
        status=status,
        contact_id=contact_id,
        contact_query=contact_query,
        retry_filter=retry_filter,
        date_from=date_from,
        date_to=date_to,
    )
    return APIResponse.ok(data)


@router.get("/stats", response_model=APIResponse[QueueStatsDTO])
def get_queue_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns operational queue health metrics, status breakdown, stale lease count,
    Emergency Stop / Circuit Breaker status, and locked Confirmed Send Rate.
    """
    data = WebQueueService.get_queue_stats(db)
    return APIResponse.ok(data)


@router.get("/{message_id}", response_model=APIResponse[QueueMessageDetailDTO])
def get_message_detail(
    message_id: int = Path(..., ge=1, description="Message ID to inspect"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves complete inspection details for a single message record.
    Includes lifecycle timestamps, retry metrics, lease state, and recent audit logs.
    """
    data = WebQueueService.get_message_detail(db=db, user=current_user, message_id=message_id)
    return APIResponse.ok(data)


@router.post(
    "/reconcile",
    response_model=APIResponse[QueueReconcileResponse],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def reconcile_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reconciles stale worker leases using authoritative Phase 3 PersistentQueueService.
    Resets expired leases to claimable queue states and records an immutable audit log.
    """
    data = WebQueueService.reconcile_queue(db=db, user=current_user)
    return APIResponse.ok(data)


@router.post(
    "/{message_id}/cancel",
    response_model=APIResponse[QueueMessageDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def cancel_message(
    message_id: int = Path(..., ge=1, description="Message ID to cancel"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cancels a message from non-terminal states (PENDING, QUEUED, RETRY_PENDING).
    Uses authoritative Phase 3 cancellation and records an immutable audit log.
    """
    data = WebQueueService.cancel_message(db=db, user=current_user, message_id=message_id)
    return APIResponse.ok(data)


@router.post(
    "/{message_id}/resolve-unknown",
    response_model=APIResponse[QueueMessageDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def resolve_unknown_outcome(
    req: UnknownOutcomeResolveRequest,
    message_id: int = Path(..., ge=1, description="Message ID to reconcile"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Strict manual reconciliation for UNKNOWN_OUTCOME messages.
    Requires external verification, explicit explanation reason, and exact
    confirmation phrase 'CONFIRM-NOT-DELIVERED'. Resets message to QUEUED
    and records an immutable audit log.
    """
    data = WebQueueService.resolve_unknown_outcome(
        db=db,
        user=current_user,
        message_id=message_id,
        request=req
    )
    return APIResponse.ok(data)
