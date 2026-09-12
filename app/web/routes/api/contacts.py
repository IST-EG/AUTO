"""
API Router for Contacts and CSV operations.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Path, UploadFile, File, Response
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.web.dependencies import (
    get_db,
    get_current_user,
    require_operator,
    verify_csrf,
)
from app.web.schemas.common import APIResponse
from app.web.schemas.contacts import (
    ContactListDTO,
    ContactDetailDTO,
    ContactCreateRequest,
    ContactUpdateRequest,
    CSVImportDryRunResponse,
    CSVImportCommitResponse,
)
from app.web.services.contact_service import ContactService

router = APIRouter(prefix="/api/v1/contacts", tags=["Contacts"])


@router.get("", response_model=APIResponse[List[ContactListDTO]])
def list_contacts(
    search: Optional[str] = Query(None),
    consent_status: Optional[str] = Query(None),
    contact_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists contacts. Masks phone numbers for non-admin viewers."""
    is_privileged = current_user.role in (UserRole.ADMIN.value, UserRole.OWNER.value)
    data = ContactService.list_contacts(
        db,
        search=search,
        consent_status=consent_status,
        contact_status=contact_status,
        is_privileged=is_privileged,
        limit=limit,
        offset=offset,
    )
    return APIResponse.ok(data)


@router.post(
    "",
    response_model=APIResponse[ContactDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def create_contact(
    req: ContactCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates a new contact record."""
    data = ContactService.create_contact(db, req, username=current_user.username)
    return APIResponse.ok(data)


@router.get("/export")
def export_contacts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """Exports all active contacts as a downloadable CSV file."""
    csv_content = ContactService.export_contacts_csv(db)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=contacts_export.csv"
        }
    )


@router.get("/{contact_id}", response_model=APIResponse[ContactDetailDTO])
def get_contact(
    contact_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves full contact details."""
    data = ContactService.get_contact(db, contact_id)
    return APIResponse.ok(data)


@router.patch(
    "/{contact_id}",
    response_model=APIResponse[ContactDetailDTO],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
def update_contact(
    contact_id: int = Path(..., ge=1),
    req: ContactUpdateRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Updates contact properties."""
    data = ContactService.update_contact(
        db, contact_id, req, username=current_user.username
    )
    return APIResponse.ok(data)


@router.post(
    "/import/dry-run",
    response_model=APIResponse[CSVImportDryRunResponse],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
async def dry_run_csv_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Validates CSV file for import without writing changes to the database.
    Returns preview rows and counts of valid, invalid, duplicate, and missing rows.
    """
    raw_bytes = await file.read()
    content = raw_bytes.decode("utf-8-sig", errors="replace")
    data = ContactService.dry_run_csv(content, db)
    return APIResponse.ok(data)


@router.post(
    "/import/commit",
    response_model=APIResponse[CSVImportCommitResponse],
    dependencies=[Depends(require_operator), Depends(verify_csrf)],
)
async def commit_csv_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Commits CSV file import into the contacts database."""
    raw_bytes = await file.read()
    content = raw_bytes.decode("utf-8-sig", errors="replace")
    data = ContactService.commit_csv(content, db, username=current_user.username)
    return APIResponse.ok(data)
