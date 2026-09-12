"""
Runner Control API endpoints for Web Control Center.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.web.dependencies import (
    get_db,
    get_current_user,
    require_operator,
    verify_csrf,
)
from app.web.schemas.common import APIResponse
from app.web.schemas.runner import (
    RunnerStartRequest,
    RunnerStopRequest,
    RunnerStatusDTO,
)
from app.web.services.runner_control_service import RunnerControlService

router = APIRouter(prefix="/api/v1/runner", tags=["Runner"])


@router.get("/status", response_model=APIResponse[RunnerStatusDTO])
def get_runner_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns current production runner state from authoritative OS file lock.
    Accessible to all authenticated roles.
    """
    status_data = RunnerControlService.get_status(db)
    return APIResponse.ok(status_data)


@router.post("/start", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def start_runner(
    payload: RunnerStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator)
):
    """
    Initiates background production runner for the target campaign.
    Requires at least OPERATOR role and valid CSRF token.
    Enforces OS process lock singularity and preflight checks.
    """
    success, message, data = RunnerControlService.start_runner(
        db=db,
        campaign_id=payload.campaign_id,
        operator_username=current_user.username
    )

    if not success:
        if "conflict" in message.lower() or "already" in message.lower() or "singularity" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message
            )
        elif "not found" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )

    return APIResponse.ok(data, meta={"message": message})


@router.post("/stop", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def stop_runner(
    payload: RunnerStopRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator)
):
    """
    Sends SIGTERM to active runner process to stop it gracefully.
    Requires at least OPERATOR role and valid CSRF token.
    """
    success, message, data = RunnerControlService.stop_runner(
        db=db,
        operator_username=current_user.username,
        timeout_seconds=payload.timeout_seconds
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message
        )

    return APIResponse.ok(data or {}, meta={"message": message})
