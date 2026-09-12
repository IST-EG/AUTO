"""
Phone number utility functions providing E.164 format validation and normalization.

This module exports thin wrapper functions around the PhoneValidator class.
"""

from app.contacts.validator import PhoneValidator

_validator = PhoneValidator()


def normalize(phone: str, country_code: str) -> str:
    """
    Normalize an arbitrary phone string to E.164 format.

    Accepts either a 2-letter ISO region code (e.g., 'EG') or
    a numeric dialling prefix (e.g., '20') as the country_code hint.

    Args:
        phone: Phone number string in any format.
        country_code: Either 2-letter ISO region code or numeric dialling prefix.

    Returns:
        Normalized phone number in E.164 format (+<digits>).

    Raises:
        ValidationError: If phone cannot be normalized to a valid E.164 format.
    """
    return _validator.normalize_to_e164(phone, country_code)


def is_valid_e164(phone: str) -> bool:
    """
    Validate that a phone string is in valid E.164 format.

    E.164 format: '+' prefix followed by 7-15 digits, no spaces or special chars.

    Args:
        phone: Phone string to validate.

    Returns:
        True if phone is valid E.164 format, False otherwise.
    """
    return _validator.validate_e164(phone)


def extract_country_code(phone: str) -> str:
    """
    Extract the numeric country dialling code from an E.164-format phone string.

    Args:
        phone: Phone number in E.164 format (must start with '+').

    Returns:
        Numeric country code as a string (e.g., '20' for Egypt).

    Raises:
        ValidationError: If phone is not valid E.164 format.
    """
    return _validator.parse_country_code(phone)
