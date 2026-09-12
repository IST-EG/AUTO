"""
Web service for Contact Management and CSV ingestion/export.
"""

import io
import tempfile
import os
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.contact import Contact
from app.models.audit_log import AuditLog
from app.contacts.manager import ContactManager
from app.contacts.validator import PhoneValidator
from app.contacts.csv_handler import CSVHandler
from app.contacts.exceptions import (
    ContactNotFoundError,
    DuplicateContactError,
    ValidationError as ContactValidationError,
)
from app.web.schemas.contacts import (
    ContactCreateRequest,
    ContactUpdateRequest,
    ContactListDTO,
    ContactDetailDTO,
    CSVImportDryRunResponse,
    CSVImportCommitResponse,
)


def mask_phone_number(phone: str) -> str:
    """Masks middle characters of phone number for non-privileged views."""
    if not phone or len(phone) <= 6:
        return phone
    # Keep dial prefix (first 4) and last 3 chars, mask the rest
    prefix = phone[:4]
    suffix = phone[-3:]
    masked_part = "*" * max(3, len(phone) - 7)
    return f"{prefix}{masked_part}{suffix}"


class ContactService:
    """Service handling contact management, masking, and CSV operations."""

    @staticmethod
    def list_contacts(
        db: Session,
        search: Optional[str] = None,
        consent_status: Optional[str] = None,
        contact_status: Optional[str] = None,
        is_privileged: bool = False,
        limit: int = 100,
        offset: int = 0
    ) -> List[ContactListDTO]:
        """Lists contacts with filtering and optional privacy masking."""
        query = db.query(Contact)

        if consent_status:
            query = query.filter(Contact.consent_status == consent_status)
        if contact_status:
            query = query.filter(Contact.contact_status == contact_status)

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Contact.name.ilike(term),
                    Contact.phone_e164.ilike(term),
                    Contact.company.ilike(term),
                    Contact.city.ilike(term)
                )
            )

        contacts = query.order_by(Contact.id.desc()).offset(offset).limit(limit).all()

        results = []
        for c in contacts:
            phone = c.phone_e164 if is_privileged else mask_phone_number(c.phone_e164)
            results.append(
                ContactListDTO(
                    id=c.id,
                    name=c.name,
                    phone_e164=phone,
                    country_code=c.country_code,
                    company=c.company,
                    city=c.city,
                    consent_status=c.consent_status,
                    contact_status=c.contact_status,
                    messages_sent_count=c.messages_sent_count,
                    created_at=c.created_at,
                )
            )
        return results

    @staticmethod
    def get_contact(db: Session, contact_id: int) -> ContactDetailDTO:
        """Retrieves detailed contact profile."""
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Contact {contact_id} not found."
            )

        return ContactDetailDTO(
            id=contact.id,
            name=contact.name,
            phone_e164=contact.phone_e164,
            country_code=contact.country_code,
            company=contact.company,
            city=contact.city,
            consent_status=contact.consent_status,
            contact_status=contact.contact_status,
            is_duplicate=contact.is_duplicate,
            messages_sent_count=contact.messages_sent_count,
            last_contacted_at=contact.last_contacted_at,
            opted_in_at=contact.opted_in_at,
            opted_out_at=contact.opted_out_at,
            created_at=contact.created_at,
            updated_at=contact.updated_at,
        )

    @staticmethod
    def create_contact(db: Session, req: ContactCreateRequest, username: Optional[str] = None) -> ContactDetailDTO:
        """Creates a single contact."""
        mgr = ContactManager(db)
        try:
            contact = mgr.create_contact(
                name=req.name,
                phone_number=req.phone_number,
                country_code=req.country_code,
                company=(req.company or "").strip(),
                city=(req.city or "").strip(),
                consent_status=req.consent_status,
            )
        except DuplicateContactError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc)
            )
        except ContactValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc)
            )

        # Audit log
        audit = AuditLog(
            event_type="CONTACT_CREATED",
            contact_id=contact.id,
            status="SUCCESS",
            result=f"Contact {contact.id} created by {username or 'anonymous'}",
        )
        db.add(audit)
        db.commit()

        return ContactService.get_contact(db, contact.id)

    @staticmethod
    def update_contact(
        db: Session,
        contact_id: int,
        req: ContactUpdateRequest,
        username: Optional[str] = None
    ) -> ContactDetailDTO:
        """Updates contact properties."""
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Contact {contact_id} not found."
            )

        if req.name is not None:
            contact.name = req.name.strip()
        if req.company is not None:
            contact.company = req.company.strip() or None
        if req.city is not None:
            contact.city = req.city.strip() or None
        if req.consent_status is not None:
            contact.consent_status = req.consent_status
        if req.contact_status is not None:
            contact.contact_status = req.contact_status

        audit = AuditLog(
            event_type="CONTACT_UPDATED",
            contact_id=contact.id,
            status="SUCCESS",
            result=f"Contact {contact.id} updated by {username or 'anonymous'}",
        )
        db.add(audit)
        db.commit()
        db.refresh(contact)

        return ContactService.get_contact(db, contact.id)

    @staticmethod
    def dry_run_csv(content: str, db: Session) -> CSVImportDryRunResponse:
        """
        Validates CSV content without persisting changes.
        Returns validation breakdown: valid rows, duplicate count, invalid phone errors.
        """
        import csv

        validator = PhoneValidator()
        reader = csv.DictReader(io.StringIO(content))

        if reader.fieldnames is None:
            return CSVImportDryRunResponse(
                total_rows=0,
                valid_count=0,
                invalid_phone_count=0,
                duplicate_count=0,
                missing_fields_count=0,
                errors=["CSV file is empty or malformed"],
                preview=[]
            )

        required_cols = ["name", "phone_number", "country_code"]
        missing_cols = [c for c in required_cols if c not in reader.fieldnames]
        if missing_cols:
            return CSVImportDryRunResponse(
                total_rows=0,
                valid_count=0,
                invalid_phone_count=0,
                duplicate_count=0,
                missing_fields_count=0,
                errors=[f"Missing required columns: {', '.join(missing_cols)}"],
                preview=[]
            )

        total_rows = 0
        valid_count = 0
        invalid_phone_count = 0
        duplicate_count = 0
        missing_fields_count = 0
        seen_phones = set()
        preview = []

        for row in reader:
            total_rows += 1
            name = (row.get("name") or "").strip()
            phone_raw = (row.get("phone_number") or "").strip()
            cc = (row.get("country_code") or "").strip()

            if not name or not phone_raw or not cc:
                missing_fields_count += 1
                continue

            try:
                norm_phone = validator.normalize_to_e164(phone_raw, cc)
            except ContactValidationError:
                invalid_phone_count += 1
                continue

            pair = (norm_phone, cc)
            if pair in seen_phones:
                duplicate_count += 1
                continue

            existing = (
                db.query(Contact.id)
                .filter(Contact.phone_e164 == norm_phone, Contact.country_code == cc)
                .first()
            )
            if existing:
                duplicate_count += 1
                continue

            seen_phones.add(pair)
            valid_count += 1
            if len(preview) < 5:
                preview.append({
                    "name": name,
                    "phone_e164": norm_phone,
                    "country_code": cc,
                    "company": (row.get("company") or "").strip() or None,
                    "city": (row.get("city") or "").strip() or None,
                })

        return CSVImportDryRunResponse(
            total_rows=total_rows,
            valid_count=valid_count,
            invalid_phone_count=invalid_phone_count,
            duplicate_count=duplicate_count,
            missing_fields_count=missing_fields_count,
            errors=[],
            preview=preview,
        )

    @staticmethod
    def commit_csv(content: str, db: Session, username: Optional[str] = None) -> CSVImportCommitResponse:
        """Executes full CSV ingestion into the database."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = f.name

        try:
            handler = CSVHandler()
            import_res = handler.import_from_csv(temp_path, db)

            audit = AuditLog(
                event_type="CONTACT_CSV_IMPORTED",
                status="SUCCESS",
                result=(
                    f"Imported {import_res.imported} contacts ({import_res.total_rows} total, "
                    f"{import_res.skipped_duplicate} dupes, {import_res.skipped_invalid_phone} invalid) "
                    f"by {username or 'anonymous'}"
                ),
            )
            db.add(audit)
            db.commit()

            return CSVImportCommitResponse(
                total_rows=import_res.total_rows,
                imported=import_res.imported,
                skipped_duplicate=import_res.skipped_duplicate,
                skipped_invalid_phone=import_res.skipped_invalid_phone,
                skipped_missing_fields=import_res.skipped_missing_fields,
                errors=import_res.errors,
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def export_contacts_csv(db: Session) -> str:
        """Exports all active contacts to standard CSV format."""
        import csv

        contacts = db.query(Contact).filter(Contact.contact_status == "active").order_by(Contact.id.asc()).all()
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=CSVHandler.EXPORT_COLUMNS)
        writer.writeheader()

        for c in contacts:
            writer.writerow({col: getattr(c, col) for col in CSVHandler.EXPORT_COLUMNS})

        return output.getvalue()
