"""
Setup & Bootstrap API endpoints.
"""

from fastapi import APIRouter, Depends, Request, HTTPException, status
from sqlalchemy.orm import Session
from app.web.dependencies import get_db
from app.web.schemas.common import APIResponse
from app.web.schemas.setup import SetupStatusData, BootstrapRequest
from app.web.services.bootstrap_service import BootstrapService

router = APIRouter(prefix="/api/v1/setup", tags=["Setup"])


@router.get("/status", response_model=APIResponse[SetupStatusData])
def get_setup_status(db: Session = Depends(get_db)):
    """Checks whether first-run OWNER bootstrap is available."""
    available = BootstrapService.is_setup_available(db)
    return APIResponse.ok(SetupStatusData(available=available))


@router.post("/bootstrap", response_model=APIResponse[dict])
def bootstrap_owner(
    request: Request,
    payload: BootstrapRequest,
    db: Session = Depends(get_db)
):
    """
    Executes one-time atomic bootstrap of the initial OWNER account.
    Fails with 409 Conflict if setup was already completed or concurrently created.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    success, message, owner_dict = BootstrapService.bootstrap_owner(
        db=db,
        username=payload.username,
        email=payload.email,
        password=payload.password,
        ip_address=client_ip
    )

    if not success:
        if "already" in message.lower() or "conflict" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message
        )

    return APIResponse.ok(owner_dict, meta={"message": message})
