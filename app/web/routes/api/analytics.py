"""
Analytics API endpoints for Phase 7.5 Analytics & Reporting Control Center.

Endpoints:
- GET /api/v1/analytics/overview: Executive snapshot, KPIs, timeline, campaign performance table, live queue.
- GET /api/v1/analytics/campaigns/{campaign_id}: Deep-dive campaign performance, funnel, send rates.
- GET /api/v1/analytics/queue/live: Point-in-time live queue health snapshot (zero date filtering).
- GET /api/v1/analytics/queue/historical: Bounded historical execution metrics and throughput timeline.
- GET /api/v1/analytics/export/campaign/{campaign_id}: Streaming CSV export with 5-stage fail-safe audit.
- GET /api/v1/analytics/export/summary: Streaming CSV export of campaign performance summary.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, Path, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.campaign import Campaign
from app.web.dependencies import get_db, get_current_user, require_operator
from app.web.schemas.common import APIResponse
from app.web.schemas.analytics import (
    AnalyticsOverviewResponse,
    CampaignAnalyticsResponse,
    QueueLiveAnalyticsResponse,
    QueueHistoricalAnalyticsResponse,
)
from app.web.services.analytics_web_service import AnalyticsWebService

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


@router.get("/overview", response_model=APIResponse[AnalyticsOverviewResponse])
def get_analytics_overview(
    preset: str = Query("today", description="Calendar preset: today, yesterday, last_7_days, last_30_days, custom"),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD for custom preset"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD for custom preset"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns executive overview analytics across calendar presets resolved in APP_TIMEZONE.
    Combines bounded historical metrics with point-in-time live queue snapshot.
    Accessible to all authenticated roles (VIEWER, OPERATOR, ADMIN, OWNER).
    """
    data = AnalyticsWebService.get_overview(
        db=db,
        preset=preset,
        start_date=start_date,
        end_date=end_date,
    )
    return APIResponse.ok(data)


@router.get("/campaigns/{campaign_id}", response_model=APIResponse[CampaignAnalyticsResponse])
def get_campaign_analytics(
    campaign_id: int = Path(..., ge=1, description="Campaign identifier"),
    preset: Optional[str] = Query(None, description="Optional calendar preset filter"),
    start_date: Optional[str] = Query(None, description="Optional custom start date"),
    end_date: Optional[str] = Query(None, description="Optional custom end date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns comprehensive campaign analytics, contact distribution, and send rates.
    Accessible to all authenticated roles (VIEWER, OPERATOR, ADMIN, OWNER).
    """
    data = AnalyticsWebService.get_campaign_analytics(
        db=db,
        campaign_id=campaign_id,
        preset=preset,
        start_date=start_date,
        end_date=end_date,
    )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found."
        )
    return APIResponse.ok(data)


@router.get("/queue/live", response_model=APIResponse[QueueLiveAnalyticsResponse])
def get_queue_live_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns live point-in-time queue health snapshot.
    Evaluated with ZERO date filtering.
    """
    data = AnalyticsWebService.get_queue_live(db=db)
    return APIResponse.ok(data)


@router.get("/queue/historical", response_model=APIResponse[QueueHistoricalAnalyticsResponse])
def get_queue_historical_analytics(
    preset: str = Query("today", description="Calendar preset: today, yesterday, last_7_days, last_30_days, custom"),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD for custom preset"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD for custom preset"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns historical execution analytics aggregated strictly within the time window.
    """
    data = AnalyticsWebService.get_queue_historical(
        db=db,
        preset=preset,
        start_date=start_date,
        end_date=end_date,
    )
    return APIResponse.ok(data)


@router.get("/export/campaign/{campaign_id}")
def export_campaign_csv(
    campaign_id: int = Path(..., ge=1, description="Campaign identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Streams campaign message records as CSV.
    - RBAC: Restricted strictly to OPERATOR, ADMIN, and OWNER. (VIEWER receives 403 Forbidden).
    - Phone numbers masked for OPERATOR, full E.164 for ADMIN/OWNER.
    - Message body content strictly omitted.
    - Emits ANALYTICS_REPORT_EXPORTED audit log upon clean cursor exhaustion.
    - Emits ANALYTICS_REPORT_EXPORT_FAILED audit log if interrupted.
    """
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found."
        )

    generator = AnalyticsWebService.stream_campaign_csv(
        campaign_id=campaign_id,
        user=current_user,
        db=db,
    )

    filename = f"campaign_{campaign_id}_analytics_export.csv"
    return StreamingResponse(
        generator,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/export/summary")
def export_summary_csv(
    preset: str = Query("today", description="Calendar preset: today, yesterday, last_7_days, last_30_days, custom"),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Streams campaign performance summary as CSV.
    - RBAC: Restricted strictly to OPERATOR, ADMIN, and OWNER. (VIEWER receives 403 Forbidden).
    """
    generator = AnalyticsWebService.stream_summary_csv(
        preset=preset,
        user=current_user,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )

    filename = f"analytics_summary_{preset}.csv"
    return StreamingResponse(
        generator,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )
