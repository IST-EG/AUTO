"""
Contacts module for contact management, phone validation, and CSV handling.

Exports:
  - ContactManager: CRUD operations and duplicate detection
  - PhoneValidator: E.164 format validation and normalization
  - CSVHandler, ImportResult: CSV import/export
  - Custom exceptions: ValidationError, DuplicateContactError, ContactNotFoundError, DatabaseError
"""

from app.contacts.exceptions import (
    ContactNotFoundError,
    DatabaseError,
    DuplicateContactError,
    ValidationError,
)
from app.contacts.manager import ContactManager

__all__ = [
    "ContactManager",
    "ValidationError",
    "DuplicateContactError",
    "ContactNotFoundError",
    "DatabaseError",
]
