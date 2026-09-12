"""
System Operations and Emergency Stop API endpoints.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.audit_log import AuditLog
from app.web.dependencies import (
    get_db,
    get_current_user,
    require_operator,
    require_admin,
    verify_csrf,
)
from app.web.schemas.common import APIResponse
from app.web.schemas.dashboard import EmergencyStopStatusDTO
from app.web.schemas.runner import (
    EmergencyStopRequest,
    EmergencyResumeRequest,
)
from app.scheduler.emergency_stop import EmergencyStop

router = APIRouter(prefix="/api/v1/system", tags=["System"])


@router.get("/emergency-status", response_model=APIResponse[EmergencyStopStatusDTO])
def get_emergency_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns current emergency stop status and last activation details.
    Accessible to all authenticated users.
    """
    controller = EmergencyStop(db)
    active = controller.is_active()

    last_log = (
        db.query(AuditLog)
        .filter(AuditLog.event_type.in_(["EMERGENCY_STOP_ACTIVATED", "EMERGENCY_STOP_RESUMED"]))
        .order_by(AuditLog.id.desc())
        .first()
    )

    reason = (last_log.error_message or last_log.result) if (last_log and active) else None
    activated_at = last_log.created_at.isoformat() if (last_log and active and last_log.created_at) else None

    return APIResponse.ok(EmergencyStopStatusDTO(
        is_active=active,
        status="ACTIVE (HALTED)" if active else "INACTIVE (OPERATIONAL)",
        reason=reason,
        activated_at=activated_at,
    ))


@router.post("/emergency-stop", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def trigger_emergency_stop(
    payload: EmergencyStopRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator)
):
    """
    Engages system-wide emergency stop.
    Blocks all NEW queue claims in <500ms target without forcibly aborting in-flight sends.
    Requires at least OPERATOR role and valid CSRF token.
    """
    controller = EmergencyStop(db)
    reason = payload.reason or f"Emergency stop triggered by {current_user.username}"
    controller.trigger(reason=reason)

    return APIResponse.ok(
        {"is_active": True, "status": "ACTIVE (HALTED)"},
        meta={"message": "Emergency stop successfully engaged. All new message claims halted."}
    )


@router.post("/emergency-resume", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def resume_emergency_stop(
    payload: EmergencyResumeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """
    Clears emergency stop and permits normal queue dispatching to resume.
    Requires at least ADMIN role and valid CSRF token.
    """
    controller = EmergencyStop(db)
    reason = payload.reason or f"Emergency stop resumed by {current_user.username}"
    controller.resume(reason=reason)

    return APIResponse.ok(
        {"is_active": False, "status": "INACTIVE (OPERATIONAL)"},
        meta={"message": "Emergency stop cleared. Normal queue dispatching may now resume."}
    )
