"""
API Router for Message Templates.
"""

from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.models.user import User
from app.web.dependencies import (
    get_db,
    get_current_user,
    require_operator,
    verify_csrf,
)
from app.web.schemas.common import APIResponse
from app.web.schemas.templates import (
    TemplateListDTO,
    TemplateDetailDTO,
    TemplateVersionDTO,
    TemplateCreateRequest,
    TemplateVersionCreateRequest,
    TemplateValidateRequest,
    TemplateValidateResponse,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
)
from app.web.services.template_service import TemplateService

router = APIRouter(prefix="/api/v1/templates", tags=["Templates"])


@router.get("", response_model=APIResponse[List[TemplateListDTO]])
def list_templates(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists all templates with their latest version summary."""
    data = TemplateService.list_templates(db, limit=limit, offset=offset)
    return APIResponse.ok(data)


@router.post(
    "",
    response_model=APIResponse[TemplateDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def create_template(
    req: TemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates a new template and its initial immutable version 1."""
    data = TemplateService.create_template(db, req, username=current_user.username)
    return APIResponse.ok(data)


@router.post("/validate", response_model=APIResponse[TemplateValidateResponse])
def validate_template_syntax(
    req: TemplateValidateRequest,
    current_user: User = Depends(get_current_user),
):
    """Validates template body syntax against allowed variable names."""
    data = TemplateService.validate_template_syntax(req.body)
    return APIResponse.ok(data)


@router.post("/preview", response_model=APIResponse[TemplatePreviewResponse])
def preview_template(
    req: TemplatePreviewRequest,
    current_user: User = Depends(get_current_user),
):
    """Renders preview of template using mock contact and campaign."""
    data = TemplateService.preview_template(req)
    return APIResponse.ok(data)


@router.get("/{template_id}", response_model=APIResponse[TemplateDetailDTO])
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves a single template with its complete immutable version history."""
    data = TemplateService.get_template(db, template_id)
    return APIResponse.ok(data)


@router.post(
    "/{template_id}/versions",
    response_model=APIResponse[TemplateVersionDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def create_template_version(
    template_id: int,
    req: TemplateVersionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Appends a new immutable version to an existing template."""
    data = TemplateService.create_version(
        db, template_id, req, username=current_user.username
    )
    return APIResponse.ok(data)
