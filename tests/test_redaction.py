"""
Unit and security tests for sensitive data redaction and E.164/Egyptian phone masking.
"""

import json
import logging
from pathlib import Path
import pytest

from app.utils.logger import (
    mask_phone,
    sanitize_text,
    sanitize_dict,
    configure_logging,
    get_logger,
    log_event,
)
from app.utils.events import EventType


class TestPhoneMasking:
    """Test suite for E.164-aware and Egyptian phone number masking."""

    def test_egyptian_e164_masking(self):
        # Standard Egyptian mobile: +201012345678
        masked = mask_phone("+201012345678")
        assert masked == "+2010******78"
        assert len(masked) == len("+201012345678")
        assert "123456" not in masked

    def test_egyptian_local_mobile_masking(self):
        # Local format without country code: 01012345678
        masked = mask_phone("01012345678")
        assert masked == "010******78"
        assert "123456" not in masked

    def test_international_e164_numbers(self):
        # US: +14155552671
        us_masked = mask_phone("+14155552671")
        assert us_masked == "+1415*****71"
        assert "55526" not in us_masked

        # UK: +447911123456
        uk_masked = mask_phone("+447911123456")
        assert uk_masked == "+4479******56"
        assert "111234" not in uk_masked

        # Saudi Arabia: +966501234567
        ksa_masked = mask_phone("+966501234567")
        assert ksa_masked == "+9665******67"
        assert "012345" not in ksa_masked

    def test_formatted_phone_numbers(self):
        # Spaced or punctuated numbers
        masked = mask_phone("+20 101 234 5678")
        assert masked == "+2010******78"

    def test_already_masked_idempotence(self):
        # Ensure already-masked strings are never double-masked
        original = "+2010******78"
        assert mask_phone(original) == original
        assert mask_phone("+1555****123") == "+1555****123"

    def test_none_and_empty(self):
        assert mask_phone(None) == "N/A"
        assert mask_phone("") == "N/A"
        assert mask_phone("   ") == "N/A"


class TestSanitizeText:
    """Test suite for credential, token, and text sanitization."""

    def test_scrub_sensitive_key_values(self):
        samples = [
            ("api_key=sk-123456789abcdef", "api_key=[REDACTED]"),
            ("token: bearer_xyz987654", "token: [REDACTED]"),
            ("password=SuperSecretPassword123!", "password=[REDACTED]"),
            ("auth_token = jwt_secret_value", "auth_token = [REDACTED]"),
            ("cookie: wa_session=abc123xyz", "cookie: [REDACTED]"),
        ]
        for raw, expected in samples:
            assert expected in sanitize_text(raw)

    def test_jwt_token_redaction(self):
        raw = "User session token is eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c in header"
        sanitized = sanitize_text(raw)
        assert "[REDACTED]" in sanitized
        assert "eyJhbGci" not in sanitized

    def test_phone_masking_in_sentences(self):
        text = "Dispatched message to +201012345678 successfully."
        sanitized = sanitize_text(text)
        assert "+2010******78" in sanitized
        assert "123456" not in sanitized

    def test_preserves_standard_dates_and_timestamps(self):
        text = "Event occurred at 2026-09-11 17:45:00 for campaign 42"
        sanitized = sanitize_text(text)
        assert "2026-09-11 17:45:00" in sanitized
        assert "campaign 42" in sanitized


class TestSanitizeDict:
    """Test suite for structured dictionary sanitization."""

    def test_omits_message_body_content(self):
        data = {
            "message_id": 105,
            "message_content": "Hello Ahmed, your appointment is confirmed.",
            "rendered_content": "Special promo text here",
            "body": "Raw template string",
        }
        sanitized = sanitize_dict(data)
        assert sanitized["message_id"] == 105
        assert sanitized["message_content"] == f"<content_length: {len('Hello Ahmed, your appointment is confirmed.')}>"
        assert sanitized["rendered_content"] == f"<content_length: {len('Special promo text here')}>"
        assert sanitized["body"] == f"<content_length: {len('Raw template string')}>"
        assert "Hello Ahmed" not in str(sanitized)

    def test_scrubs_dictionary_credentials_and_phones(self):
        data = {
            "api_key": "secret_key_12345",
            "phone": "+201012345678",
            "recipient_phone": "+14155552671",
            "metadata": {
                "user_password": "mypassword",
                "mobile": "+447911123456",
            },
        }
        sanitized = sanitize_dict(data)
        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["phone"] == "+2010******78"
        assert sanitized["recipient_phone"] == "+1415*****71"
        assert sanitized["metadata"]["user_password"] == "[REDACTED]"
        assert sanitized["metadata"]["mobile"] == "+4479******56"


class TestChildLoggerSecurityInvariant:
    """
    Dedicated security test:
    Verify that records emitted through deeply nested child loggers are
    guaranteed to be sanitized at both persistent destinations (logs/app.log and logs/app.json.log).
    """

    def test_child_logger_propagation_sanitization(self, tmp_path):
        plain_log = tmp_path / "app.log"
        json_log = tmp_path / "app.json.log"

        # Force clean root logger configuration
        root = logging.getLogger()
        root.handlers.clear()

        configure_logging(
            log_file=str(plain_log),
            json_log_file=str(json_log),
            level="DEBUG",
        )

        # Child logger deeply nested in hierarchy
        child_logger = get_logger("app.runner.worker.subagent")

        raw_secret = "secret_super_token_999"
        raw_phone = "+201098765432"
        raw_body = "This is a confidential outbound message body."

        # Emit standard message with child logger
        child_logger.info(
            f"Processing message with api_key={raw_secret} for recipient {raw_phone}"
        )

        # Emit structured event with child logger
        log_event(
            logger=child_logger,
            level=logging.INFO,
            event=EventType.MESSAGE_SEND_CONFIRMED.value,
            component="runner",
            message=f"Confirmed dispatch for {raw_phone}",
            message_id=42,
            details={
                "api_key": raw_secret,
                "phone": raw_phone,
                "message_content": raw_body,
            },
        )

        # Flush handlers
        for h in root.handlers:
            h.flush()

        plain_content = plain_log.read_text(encoding="utf-8")
        json_content = json_log.read_text(encoding="utf-8")

        # ASSERTION 1: Neither destination contains the raw secret
        assert raw_secret not in plain_content
        assert raw_secret not in json_content
        assert "[REDACTED]" in plain_content
        assert "[REDACTED]" in json_content

        # ASSERTION 2: Neither destination contains the unmasked phone number
        assert raw_phone not in plain_content
        assert raw_phone not in json_content
        assert "+2010******32" in plain_content
        assert "+2010******32" in json_content

        # ASSERTION 3: Neither destination contains the outbound message body
        assert raw_body not in plain_content
        assert raw_body not in json_content
        assert f"<content_length: {len(raw_body)}>" in json_content
