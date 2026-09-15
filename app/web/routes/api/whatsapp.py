"""
WhatsApp Operations & Session Control REST API.

Provides endpoints for:
- Live session status & sanitized profile telemetry (GET /status)
- Sanitized preflight environmental diagnostics (GET /diagnostics)
- Observable command lifecycle inspection (GET /commands/{request_id})
- Role-gated operational mutations (POST /health-check, /reconnect, /disconnect, /logout, /commands/clear-stale)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.web.dependencies import (
    get_db,
    get_current_user,
    require_operator,
    require_admin,
    verify_csrf,
)
from app.web.schemas.common import APIResponse
from app.web.schemas.whatsapp import (
    WhatsAppStatusDTO,
    WhatsAppDiagnosticsDTO,
    WhatsAppCommandDTO,
    WhatsAppReconnectRequest,
    WhatsAppDisconnectRequest,
    WhatsAppLogoutRequest,
)
from app.web.services.whatsapp_service import WhatsAppWebService
from app.services.whatsapp_command_service import WhatsAppCommandService

router = APIRouter(prefix="/api/v1/whatsapp", tags=["WhatsApp Operations"])


@router.get("/status", response_model=APIResponse[WhatsAppStatusDTO])
def get_whatsapp_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns live WhatsApp session state, runner coupling, and sanitized profile status.
    Accessible to all authenticated roles (VIEWER, OPERATOR, ADMIN, OWNER).
    Zero Worker host filesystem paths are returned.
    """
    status_data = WhatsAppWebService.get_status(db)
    return APIResponse.ok(status_data)


@router.get("/diagnostics", response_model=APIResponse[WhatsAppDiagnosticsDTO])
def get_whatsapp_diagnostics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns sanitized preflight readiness diagnostics.
    Accessible to all authenticated roles.
    Zero Worker host filesystem paths are returned.
    """
    diag_data = WhatsAppWebService.get_diagnostics(db)
    return APIResponse.ok(diag_data)


@router.get("/commands/{request_id}", response_model=APIResponse[WhatsAppCommandDTO])
def get_whatsapp_command_status(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Inspects observable lifecycle status of a specific command by request_id.
    Accessible to all authenticated roles.
    """
    cmd = WhatsAppCommandService.get_command_by_id(db, request_id)
    if not cmd:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Command with request_id '{request_id}' not found.",
        )
    return APIResponse.ok(cmd)


@router.post("/health-check", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def request_health_check(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Submits a provider health check probe command.
    Role: OPERATOR, ADMIN, OWNER. Requires CSRF.
    Enforces single-inflight command serialization (HTTP 409 Conflict if busy).
    """
    success, message, data = WhatsAppCommandService.submit_command(
        db=db,
        action="HEALTH_CHECK",
        requested_by=current_user.username,
        requested_by_id=current_user.id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )
    return APIResponse.ok(data, meta={"message": message})


@router.post("/reconnect", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def request_reconnect(
    payload: WhatsAppReconnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Submits a safe session restart/recovery command using cached credentials.
    Role: OPERATOR, ADMIN, OWNER. Requires CSRF.
    Enforces single-inflight command serialization (HTTP 409 Conflict if busy).
    """
    success, message, data = WhatsAppCommandService.submit_command(
        db=db,
        action="RECONNECT",
        requested_by=current_user.username,
        requested_by_id=current_user.id,
        params={"reason": payload.reason},
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )
    return APIResponse.ok(data, meta={"message": message})


@router.post("/disconnect", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def request_disconnect(
    payload: WhatsAppDisconnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Submits a controlled graceful shutdown command for the browser session.
    Role: OPERATOR, ADMIN, OWNER. Requires CSRF.
    Enforces single-inflight command serialization (HTTP 409 Conflict if busy).
    """
    success, message, data = WhatsAppCommandService.submit_command(
        db=db,
        action="DISCONNECT",
        requested_by=current_user.username,
        requested_by_id=current_user.id,
        params={"reason": payload.reason},
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )
    return APIResponse.ok(data, meta={"message": message})


@router.post("/logout", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def request_logout(
    payload: WhatsAppLogoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Submits a session logout command with optional persistent profile cache clearance.
    Restricted to ADMIN and OWNER roles. Requires CSRF.
    Enforces confirmation phrase 'CONFIRM-LOGOUT'.
    """
    if payload.confirm_phrase != "CONFIRM-LOGOUT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation phrase must match exactly 'CONFIRM-LOGOUT'.",
        )

    success, message, data = WhatsAppCommandService.submit_command(
        db=db,
        action="LOGOUT",
        requested_by=current_user.username,
        requested_by_id=current_user.id,
        params={"clear_cache": payload.clear_cache, "reason": payload.reason},
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )
    return APIResponse.ok(data, meta={"message": message})


@router.post("/commands/clear-stale", response_model=APIResponse[dict], dependencies=[Depends(verify_csrf)])
def clear_stale_command(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Administrative recovery endpoint allowing ADMIN/OWNER to clear a stuck active command.
    Requires CSRF.
    """
    success, message = WhatsAppCommandService.clear_stale_command(
        db=db,
        operator_username=current_user.username,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )
    return APIResponse.ok({"cleared": success}, meta={"message": message})
