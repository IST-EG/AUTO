"""
Health & Readiness API endpoints.
"""

from fastapi import APIRouter, Depends, status, Response
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.web.dependencies import get_db
from app.web.schemas.common import APIResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=APIResponse[dict])
@router.get("/api/v1/health/live", response_model=APIResponse[dict])
def health_live():
    """Liveness probe suitable for systemd, reverse proxy, or orchestrators."""
    return APIResponse.ok({"status": "live", "service": "web_control_center"})


@router.get("/api/v1/health/ready", response_model=APIResponse[dict])
def health_ready(response: Response, db: Session = Depends(get_db)):
    """Readiness probe checking database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return APIResponse.ok({"status": "ready", "database": "connected"})
    except Exception as ex:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return APIResponse.fail(
            code="SERVICE_UNAVAILABLE",
            message="Database readiness check failed",
            details=str(ex)
        )
