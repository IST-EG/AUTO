"""
Custom exception classes for contact and validation operations.

These exceptions provide structured error handling with specific attributes
for different failure scenarios.
"""


class ValidationError(ValueError):
    """
    Raised when validation of input data fails.
    
    Attributes:
        field: The field name that failed validation
        reason: Human-readable description of why validation failed
    """
    
    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"Validation error in {field}: {reason}")


class DuplicateContactError(ValidationError):
    """
    Raised when attempting to create a contact with a duplicate phone number.
    
    Attributes:
        field: Always "phone_number" for this exception
        reason: Description of the duplicate
        phone: The duplicate phone number (E.164 format)
    """
    
    def __init__(self, phone: str, reason: str = None):
        self.phone = phone
        if reason is None:
            reason = f"Contact with phone {phone} already exists"
        super().__init__("phone_number", reason)


class ContactNotFoundError(LookupError):
    """
    Raised when attempting to access a contact that doesn't exist.
    
    Attributes:
        contact_id: The ID that was not found
    """
    
    def __init__(self, contact_id: int):
        self.contact_id = contact_id
        super().__init__(f"Contact with ID {contact_id} not found")


class DatabaseError(RuntimeError):
    """
    Raised when a SQLAlchemy database operation fails unexpectedly.
    
    Wraps SQLAlchemyError to provide a more user-friendly interface.
    """
    
    def __init__(self, message: str):
        super().__init__(f"Database error: {message}")
