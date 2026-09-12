"""
CSV import and export for contacts.

Handles reading CSV files for bulk contact import and writing contacts to CSV
for export, with per-reason skip counting for failed rows.
"""

import csv
from dataclasses import dataclass, field
from typing import List

from sqlalchemy.orm import Session

from app.contacts.exceptions import ValidationError
from app.contacts.validator import PhoneValidator
from app.models import Contact


@dataclass
class ImportResult:
    """Result of a CSV import operation."""
    
    total_rows: int = 0
    """Total number of rows in the CSV file (excluding header)."""
    
    imported: int = 0
    """Number of contacts successfully imported."""
    
    skipped_duplicate: int = 0
    """Number of rows skipped because the phone+country already exists."""
    
    skipped_invalid_phone: int = 0
    """Number of rows skipped because phone validation failed."""
    
    skipped_missing_fields: int = 0
    """Number of rows skipped because required fields were empty."""
    
    errors: List[str] = field(default_factory=list)
    """List of error messages encountered (e.g., missing CSV columns)."""
    
    @property
    def total_skipped(self) -> int:
        """Total number of rows skipped."""
        return (
            self.skipped_duplicate
            + self.skipped_invalid_phone
            + self.skipped_missing_fields
        )
    
    def __str__(self) -> str:
        """Human-readable summary of the import result."""
        lines = [
            f"Import completed:",
            f"  Total rows: {self.total_rows}",
            f"  Imported: {self.imported}",
            f"  Total skipped: {self.total_skipped}",
        ]
        if self.skipped_duplicate > 0:
            lines.append(f"    - Duplicate phone: {self.skipped_duplicate}")
        if self.skipped_invalid_phone > 0:
            lines.append(f"    - Invalid phone: {self.skipped_invalid_phone}")
        if self.skipped_missing_fields > 0:
            lines.append(f"    - Missing fields: {self.skipped_missing_fields}")
        if self.errors:
            lines.append("  Errors:")
            for error in self.errors:
                lines.append(f"    - {error}")
        return "\n".join(lines)


class CSVHandler:
    """Handles CSV import and export of contacts."""
    
    EXPORT_COLUMNS = [
        "id",
        "name",
        "phone_e164",
        "country_code",
        "company",
        "city",
        "consent_status",
        "opted_in_at",
        "opted_out_at",
        "last_contacted_at",
        "messages_sent_count",
        "contact_status",
        "is_duplicate",
        "duplicate_of_id",
        "created_at",
        "updated_at",
    ]
    
    REQUIRED_IMPORT_COLUMNS = ["name", "phone_number", "country_code"]
    
    def __init__(self):
        """Initialize CSVHandler with a PhoneValidator instance."""
        self.validator = PhoneValidator()
    
    def import_from_csv(self, file_path: str, db: Session) -> ImportResult:
        """
        Import contacts from a CSV file.
        
        CSV must have columns: name, phone_number, country_code
        Optional columns: company, city, consent_status
        
        Args:
            file_path: Path to the CSV file to import
            db: SQLAlchemy session for database operations
            
        Returns:
            ImportResult with import statistics and errors
        """
        result = ImportResult()
        rows_to_insert = []
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                
                # Check for required columns
                if reader.fieldnames is None:
                    result.errors.append("CSV file is empty or malformed")
                    return result
                
                missing_columns = [
                    col for col in self.REQUIRED_IMPORT_COLUMNS
                    if col not in reader.fieldnames
                ]
                if missing_columns:
                    result.errors.append(
                        f"Missing required columns: {', '.join(missing_columns)}"
                    )
                    return result
                
                # Process rows
                for row_num, row in enumerate(reader, start=2):  # start=2 (1=header)
                    result.total_rows += 1
                    
                    # Check required fields are non-empty
                    name = row.get("name", "").strip()
                    phone_number = row.get("phone_number", "").strip()
                    country_code = row.get("country_code", "").strip()
                    
                    if not name or not phone_number or not country_code:
                        result.skipped_missing_fields += 1
                        continue
                    
                    # Normalize phone to E.164
                    try:
                        phone_e164 = self.validator.normalize_to_e164(
                            phone_number, country_code
                        )
                    except ValidationError:
                        result.skipped_invalid_phone += 1
                        continue
                    
                    # Check for duplicate in database
                    existing = db.query(Contact).filter(
                        Contact.phone_e164 == phone_e164,
                        Contact.country_code == country_code,
                    ).first()
                    
                    if existing:
                        result.skipped_duplicate += 1
                        continue
                    
                    # Check for duplicate within this import batch
                    if any(
                        c.phone_e164 == phone_e164 and c.country_code == country_code
                        for c in rows_to_insert
                    ):
                        result.skipped_duplicate += 1
                        continue
                    
                    # Create new Contact
                    contact = Contact(
                        name=name,
                        phone_e164=phone_e164,
                        country_code=country_code,
                        company=row.get("company", "").strip() or None,
                        city=row.get("city", "").strip() or None,
                        consent_status=row.get("consent_status", "pending").strip() or "pending",
                    )
                    rows_to_insert.append(contact)
                
                # Insert all valid rows
                for contact in rows_to_insert:
                    db.add(contact)
                
                db.commit()
                result.imported = len(rows_to_insert)
        
        except FileNotFoundError:
            result.errors.append(f"File not found: {file_path}")
        except PermissionError:
            result.errors.append(f"Permission denied reading file: {file_path}")
        except Exception as e:
            db.rollback()
            result.errors.append(f"Unexpected error: {str(e)}")
        
        return result
    
    def export_to_csv(self, contacts: list, file_path: str) -> int:
        """
        Export contacts to a CSV file.
        
        Args:
            contacts: List of Contact ORM instances to export
            file_path: Path where CSV will be written
            
        Returns:
            Number of rows written (excluding header)
        """
        row_count = 0
        
        try:
            with open(file_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.EXPORT_COLUMNS)
                writer.writeheader()
                
                for contact in contacts:
                    row = {col: getattr(contact, col) for col in self.EXPORT_COLUMNS}
                    writer.writerow(row)
                    row_count += 1
        
        except Exception as e:
            raise IOError(f"Failed to write CSV file {file_path}: {str(e)}")
        
        return row_count
