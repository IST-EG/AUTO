"""
Phone number validation and normalization using the phonenumbers library.

Enforces E.164 format and provides utilities for parsing and validating
phone numbers against international standards.
"""

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

from app.contacts.exceptions import ValidationError


class PhoneValidator:
    """
    Validates and normalizes phone numbers to E.164 format.
    
    E.164 format: + followed by 7-15 digits (no spaces, special chars, or extensions)
    """
    
    def validate_e164(self, phone: str) -> bool:
        """
        Validate that a phone string is already in E.164 format.
        
        Args:
            phone: Phone number string to validate
            
        Returns:
            True if phone is a valid E.164 number, False otherwise
            
        Note:
            Returns False (does not raise) on parse errors.
        """
        if not phone or not isinstance(phone, str):
            return False
        
        try:
            parsed = phonenumbers.parse(phone, None)
            is_valid = phonenumbers.is_valid_number(parsed)
            
            # Verify it's actually in E.164 format (starts with +)
            if is_valid and not phone.startswith("+"):
                return False
            
            # Verify E.164 constraint: 7-15 digits after the +
            if is_valid and len(phone) < 9 or len(phone) > 16:  # +X...X (min 7 digits, max 15)
                return False
            
            return is_valid
        except (NumberParseException, Exception):
            return False
    
    def normalize_to_e164(self, phone: str, country_code: str) -> str:
        """
        Normalize a phone number to E.164 format.
        
        Args:
            phone: Phone number string (may be local format, national, or E.164)
            country_code: Either a 2-letter ISO region code (e.g., "EG") or
                         a numeric dialling prefix (e.g., "20" for Egypt)
        
        Returns:
            Phone number in E.164 format
            
        Raises:
            ValidationError: If the phone number cannot be parsed or is invalid
        """
        if not phone or not isinstance(phone, str):
            raise ValidationError(
                field="phone_number",
                reason="Phone number cannot be empty"
            )
        
        # Resolve numeric country code to ISO region if needed
        region = country_code
        if country_code and country_code.isdigit():
            # country_code is numeric (e.g., "20" for Egypt)
            # Convert to ISO region code
            try:
                # Try to find the region by dialling prefix
                # For common cases, we'll use a simple mapping
                # phonenumbers doesn't have a built-in reverse lookup, so we parse with None
                # and let it try to figure out the region from the phone
                region = self._numeric_to_iso(country_code)
            except Exception:
                region = None
        
        try:
            # Parse with the region hint
            parsed = phonenumbers.parse(phone, region)
            
            # Validate the parsed number
            if not phonenumbers.is_valid_number(parsed):
                raise ValidationError(
                    field="phone_number",
                    reason=f"Invalid phone number: {phone}"
                )
            
            # Format to E.164
            formatted = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
            return formatted
            
        except NumberParseException as e:
            raise ValidationError(
                field="phone_number",
                reason=f"Could not parse phone number: {str(e)}"
            )
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                field="phone_number",
                reason=f"Phone validation error: {str(e)}"
            )
    
    def parse_country_code(self, phone: str) -> str:
        """
        Extract the numeric country dialling code from an E.164 phone string.
        
        Args:
            phone: Phone number in E.164 format (e.g., "+201001234567")
            
        Returns:
            Numeric country code (e.g., "20" for Egypt)
            
        Raises:
            ValidationError: If phone is not a valid E.164 format
        """
        if not phone or not isinstance(phone, str):
            raise ValidationError(
                field="phone_number",
                reason="Phone number cannot be empty"
            )
        
        if not phone.startswith("+"):
            raise ValidationError(
                field="phone_number",
                reason=f"Phone must be in E.164 format starting with +: {phone}"
            )
        
        try:
            parsed = phonenumbers.parse(phone, None)
            if not phonenumbers.is_valid_number(parsed):
                raise ValidationError(
                    field="phone_number",
                    reason=f"Invalid E.164 phone number: {phone}"
                )
            
            # Extract country code as string
            country_code = str(parsed.country_code)
            return country_code
            
        except NumberParseException as e:
            raise ValidationError(
                field="phone_number",
                reason=f"Invalid E.164 format: {str(e)}"
            )
        except ValidationError:
            raise
    
    @staticmethod
    def _numeric_to_iso(numeric_code: str) -> str:
        """
        Convert numeric country code to ISO region code.
        
        Args:
            numeric_code: Numeric dialling prefix (e.g., "20" for Egypt)
            
        Returns:
            ISO-3166 alpha-2 region code (e.g., "EG")
        """
        # Common country code mappings
        # This is a simple mapping for common cases
        numeric_to_iso = {
            "1": "US",      # USA/Canada
            "20": "EG",     # Egypt
            "212": "MA",    # Morocco
            "216": "TN",    # Tunisia
            "44": "GB",     # UK
            "33": "FR",     # France
            "49": "DE",     # Germany
            "39": "IT",     # Italy
            "34": "ES",     # Spain
            "91": "IN",     # India
            "86": "CN",     # China
            "81": "JP",     # Japan
            "61": "AU",     # Australia
        }
        return numeric_to_iso.get(numeric_code, None)
