"""
Web service for Message Template Management.
"""

from typing import List, Optional
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.template import MessageTemplate, MessageTemplateVersion
from app.models.audit_log import AuditLog
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.campaigns.template_service import MessageTemplateService
from app.campaigns.exceptions import TemplateValidationError
from app.web.schemas.templates import (
    TemplateCreateRequest,
    TemplateVersionCreateRequest,
    TemplateValidateRequest,
    TemplateValidateResponse,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateListDTO,
    TemplateDetailDTO,
    TemplateVersionDTO,
)


class TemplateService:
    """Service handling template library operations and versioning."""

    @staticmethod
    def list_templates(db: Session, limit: int = 100, offset: int = 0) -> List[TemplateListDTO]:
        """Lists all templates with latest version summary."""
        templates = (
            db.query(MessageTemplate)
            .order_by(MessageTemplate.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        result = []
        for t in templates:
            versions = t.versions
            version_count = len(versions)
            latest = versions[-1] if versions else None
            result.append(
                TemplateListDTO(
                    id=t.id,
                    name=t.name,
                    description=t.description,
                    version_count=version_count,
                    latest_version=latest.version_number if latest else 0,
                    latest_body=latest.body if latest else "",
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
            )
        return result

    @staticmethod
    def get_template(db: Session, template_id: int) -> TemplateDetailDTO:
        """Retrieves template details with all versions."""
        template = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {template_id} not found."
            )
        versions = [
            TemplateVersionDTO(
                id=v.id,
                template_id=v.template_id,
                version_number=v.version_number,
                body=v.body,
                created_by=v.created_by,
                created_at=v.created_at,
            )
            for v in template.versions
        ]
        return TemplateDetailDTO(
            id=template.id,
            name=template.name,
            description=template.description,
            versions=versions,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )

    @staticmethod
    def create_template(db: Session, req: TemplateCreateRequest, username: Optional[str] = None) -> TemplateDetailDTO:
        """Creates a new template container and initial version 1."""
        # 1. Validate template syntax
        try:
            MessageTemplateService.validate_template(req.body)
        except TemplateValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc)
            )

        # 2. Check name uniqueness
        existing = db.query(MessageTemplate).filter(MessageTemplate.name == req.name.strip()).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Template with name '{req.name.strip()}' already exists."
            )

        template = MessageTemplate(
            name=req.name.strip(),
            description=req.description.strip() if req.description else None,
        )
        db.add(template)
        db.flush()

        # 3. Create initial version (v1)
        v1 = MessageTemplateVersion(
            template_id=template.id,
            version_number=1,
            body=req.body,
            created_by=username,
        )
        db.add(v1)

        # 4. Audit log
        audit = AuditLog(
            event_type="TEMPLATE_CREATED",
            status="SUCCESS",
            result=f"Template '{template.name}' created with version 1 by {username or 'anonymous'}",
        )
        db.add(audit)

        try:
            db.commit()
            db.refresh(template)
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template name conflict."
            )

        return TemplateService.get_template(db, template.id)

    @staticmethod
    def create_version(db: Session, template_id: int, req: TemplateVersionCreateRequest, username: Optional[str] = None) -> TemplateVersionDTO:
        """Appends a new immutable version to an existing template."""
        template = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {template_id} not found."
            )

        # Validate syntax
        try:
            MessageTemplateService.validate_template(req.body)
        except TemplateValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc)
            )

        next_version = 1
        if template.versions:
            next_version = max(v.version_number for v in template.versions) + 1

        new_version = MessageTemplateVersion(
            template_id=template.id,
            version_number=next_version,
            body=req.body,
            created_by=username,
        )
        template.updated_at = datetime.now(timezone.utc)
        db.add(new_version)

        # Audit log
        audit = AuditLog(
            event_type="TEMPLATE_VERSION_CREATED",
            status="SUCCESS",
            result=f"Template '{template.name}' version {next_version} added by {username or 'anonymous'}",
        )
        db.add(audit)

        db.commit()
        db.refresh(new_version)

        return TemplateVersionDTO(
            id=new_version.id,
            template_id=new_version.template_id,
            version_number=new_version.version_number,
            body=new_version.body,
            created_by=new_version.created_by,
            created_at=new_version.created_at,
        )

    @staticmethod
    def validate_template_syntax(body: str) -> TemplateValidateResponse:
        """Validates template body variables against allowed set."""
        try:
            MessageTemplateService.validate_template(body)
            found_vars = list(set(MessageTemplateService.VAR_PATTERN.findall(body)))
            return TemplateValidateResponse(valid=True, variables=found_vars, errors=[])
        except TemplateValidationError as exc:
            found_vars = list(set(MessageTemplateService.VAR_PATTERN.findall(body)))
            return TemplateValidateResponse(
                valid=False,
                variables=found_vars,
                errors=[str(exc)]
            )

    @staticmethod
    def preview_template(req: TemplatePreviewRequest) -> TemplatePreviewResponse:
        """Renders preview of template using mock contact and campaign."""
        # Create ephemeral contact and campaign for preview
        dummy_contact = Contact(
            name=req.contact_name or "John Doe",
            phone_e164="+15551234567",
            country_code="1",
            company=req.company or "Acme Corp",
            city=req.city or "Cairo",
        )
        dummy_campaign = Campaign(
            name=req.campaign_name or "Sample Campaign",
            message_template=req.body,
        )
        try:
            rendered = MessageTemplateService.render_message(req.body, dummy_contact, dummy_campaign)
            return TemplatePreviewResponse(rendered_text=rendered)
        except TemplateValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc)
            )
