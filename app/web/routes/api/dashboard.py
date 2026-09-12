"""
Dashboard API endpoints for Web Control Center.
"""

from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.user import User
from app.web.dependencies import get_db, get_current_user
from app.web.schemas.common import APIResponse
from app.web.schemas.dashboard import (
    DashboardSnapshotDTO,
    SystemHealthDTO,
    QueueSummaryDTO,
    ActiveCampaignDTO,
    RunnerSummaryDTO,
)
from app.web.services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=APIResponse[DashboardSnapshotDTO])
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns the complete aggregated operational snapshot.
    Accessible to all authenticated roles (VIEWER, OPERATOR, ADMIN, OWNER).
    """
    snapshot = DashboardService.get_dashboard_snapshot(db)
    return APIResponse.ok(snapshot)


@router.get("/health", response_model=APIResponse[SystemHealthDTO])
def get_dashboard_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns Phase 6 4-state system health status."""
    snapshot = DashboardService.get_dashboard_snapshot(db)
    return APIResponse.ok(snapshot["system_health"])


@router.get("/queue", response_model=APIResponse[QueueSummaryDTO])
def get_dashboard_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns queue breakdown, in-flight leases, and throughput."""
    snapshot = DashboardService.get_dashboard_snapshot(db)
    return APIResponse.ok(snapshot["queue"])


@router.get("/campaign", response_model=APIResponse[Optional[ActiveCampaignDTO]])
def get_dashboard_active_campaign(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns active campaign telemetry and confirmed send rate."""
    snapshot = DashboardService.get_dashboard_snapshot(db)
    return APIResponse.ok(snapshot["active_campaign"])


@router.get("/runner", response_model=APIResponse[RunnerSummaryDTO])
def get_dashboard_runner(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns runner process state, PID, uptime, and heartbeat age."""
    snapshot = DashboardService.get_dashboard_snapshot(db)
    return APIResponse.ok(snapshot["runner"])
