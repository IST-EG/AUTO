"""
Contact Manager for CRUD operations.
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import Contact
from app.contacts.validator import PhoneValidator
from app.contacts.exceptions import ContactNotFoundError, DuplicateContactError, ValidationError

class ContactManager:
    """Manages Contact creation, retrieval, and validation."""

    def __init__(self, db: Session):
        self.db = db
        self.validator = PhoneValidator()

    def create_contact(self, name: str, phone_number: str, country_code: str, **kwargs) -> Contact:
        """
        Creates a new contact after validating and normalizing the phone number.
        """
        if not name or not name.strip():
            raise ValidationError("name", "Name cannot be empty")
        
        phone_e164 = self.validator.normalize_to_e164(phone_number, country_code)
        
        # Check for duplicate
        existing = self.db.query(Contact).filter(
            Contact.phone_e164 == phone_e164,
            Contact.country_code == country_code
        ).first()

        if existing:
            raise DuplicateContactError(f"Contact with {phone_e164} and country {country_code} already exists.")
            
        contact = Contact(
            name=name.strip(),
            phone_e164=phone_e164,
            country_code=country_code,
            company=kwargs.get("company", "").strip() or None,
            city=kwargs.get("city", "").strip() or None,
            consent_status=kwargs.get("consent_status", "pending"),
            contact_status=kwargs.get("contact_status", "active")
        )
        self.db.add(contact)
        try:
            self.db.commit()
            self.db.refresh(contact)
        except IntegrityError:
            self.db.rollback()
            raise DuplicateContactError(f"Database constraint error: duplicate contact.")
            
        return contact
    
    def get_contact(self, contact_id: int) -> Contact:
        """Retrieves a contact by ID."""
        contact = self.db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise ContactNotFoundError(f"Contact {contact_id} not found.")
        return contact

    def list_contacts(self, limit: int = 100, offset: int = 0) -> List[Contact]:
        """Lists contacts."""
        return self.db.query(Contact).limit(limit).offset(offset).all()
