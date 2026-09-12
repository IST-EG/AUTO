"""
CSRF protection module using double-submit signed tokens.

Enforces that state-changing requests (POST, PUT, PATCH, DELETE) carry a signed
CSRF token in the X-CSRF-Token header matching the outreach_csrf_token cookie.
"""

import hmac
import hashlib
import secrets
from typing import Optional, Tuple
from app.web.config import web_settings


class CSRFManager:
    """Manages generation, signing, and verification of CSRF tokens."""

    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = (secret_key or web_settings.WEB_SECRET_KEY).encode("utf-8")

    def _sign(self, token_value: str) -> str:
        """Generates HMAC-SHA256 signature for token_value."""
        return hmac.new(
            self.secret_key,
            token_value.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def generate_token(self) -> str:
        """
        Generates a new cryptographically random signed CSRF token.
        Format: <raw_value>.<hmac_signature>
        """
        raw_val = secrets.token_urlsafe(32)
        signature = self._sign(raw_val)
        return f"{raw_val}.{signature}"

    def verify_token(self, token: str) -> bool:
        """Verifies that a signed CSRF token has a valid HMAC signature."""
        if not token or "." not in token:
            return False
        parts = token.split(".", 1)
        if len(parts) != 2:
            return False
        raw_val, signature = parts
        expected_sig = self._sign(raw_val)
        return hmac.compare_digest(signature, expected_sig)

    def validate_double_submit(self, cookie_token: Optional[str], header_token: Optional[str]) -> Tuple[bool, str]:
        """
        Validates the double-submit CSRF pattern:
        1. Both cookie and header tokens must exist.
        2. Both must have valid signatures.
        3. Both must match identically via constant-time comparison.
        """
        if not cookie_token:
            return False, "Missing CSRF cookie."
        if not header_token:
            return False, "Missing X-CSRF-Token header."

        if not self.verify_token(cookie_token):
            return False, "Invalid CSRF cookie signature."
        if not self.verify_token(header_token):
            return False, "Invalid X-CSRF-Token header signature."

        if not hmac.compare_digest(cookie_token, header_token):
            return False, "CSRF token mismatch between cookie and header."

        return True, "CSRF token verified."


# Global CSRF manager instance
csrf_manager = CSRFManager()
