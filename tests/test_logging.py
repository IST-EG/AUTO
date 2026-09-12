"""
Unit tests for production logging configuration, rotation, JSON schema compliance, and resilience.
"""

import json
import logging
from pathlib import Path
import sys
import pytest

from app.utils.logger import (
    configure_logging,
    get_logger,
    log_event,
    SafeRotatingFileHandler,
)
from app.utils.events import EventType


class TestLoggingConfiguration:
    """Test suite for logging configuration and handler idempotency."""

    def test_idempotent_handler_registration(self, tmp_path):
        plain_log = tmp_path / "app.log"
        json_log = tmp_path / "app.json.log"

        root = logging.getLogger()
        root.handlers.clear()

        # Call once
        configure_logging(str(plain_log), str(json_log), "INFO")
        initial_handlers_count = len(root.handlers)
        assert initial_handlers_count == 3  # plain, json, console

        # Call again
        configure_logging(str(plain_log), str(json_log), "INFO")
        assert len(root.handlers) == initial_handlers_count

    def test_single_emission_guarantee(self, tmp_path):
        plain_log = tmp_path / "app.log"
        json_log = tmp_path / "app.json.log"

        root = logging.getLogger()
        root.handlers.clear()

        configure_logging(str(plain_log), str(json_log), "INFO")
        logger = get_logger("test_module")

        logger.info("Unique milestone event message.")

        for h in root.handlers:
            h.flush()

        plain_lines = [l for l in plain_log.read_text(encoding="utf-8").strip().splitlines() if "Unique milestone" in l]
        json_lines = [l for l in json_log.read_text(encoding="utf-8").strip().splitlines() if "Unique milestone" in l]

        assert len(plain_lines) == 1
        assert len(json_lines) == 1

    def test_structured_json_schema_contract(self, tmp_path):
        plain_log = tmp_path / "app.log"
        json_log = tmp_path / "app.json.log"

        root = logging.getLogger()
        root.handlers.clear()

        configure_logging(str(plain_log), str(json_log), "INFO")
        logger = get_logger("app.runner")

        log_event(
            logger=logger,
            level=logging.INFO,
            event=EventType.MESSAGE_SEND_CONFIRMED.value,
            component="runner",
            message="Message 108 send confirmed on WhatsApp Web.",
            correlation_id="run-6a52ccbf-108",
            campaign_id=5,
            campaign_contact_id=42,
            message_id=108,
            runner_id="runner_camp5_1789128",
            provider="WhatsAppWebProvider",
            operation="send_message",
            result="SUCCESS",
            error_code=None,
            duration_ms=3420.5,
            details={
                "phone": "+201012345678",
                "attempts": 1,
                "status": "SENT",
            },
        )

        for h in root.handlers:
            h.flush()

        lines = json_log.read_text(encoding="utf-8").strip().splitlines()
        record_json = json.loads(lines[-1])

        # Verify all schema fields
        assert "timestamp" in record_json
        assert record_json["level"] == "INFO"
        assert record_json["event"] == "MESSAGE_SEND_CONFIRMED"
        assert record_json["component"] == "runner"
        assert record_json["message"] == "Message 108 send confirmed on WhatsApp Web."
        assert record_json["correlation_id"] == "run-6a52ccbf-108"
        assert record_json["campaign_id"] == 5
        assert record_json["campaign_contact_id"] == 42
        assert record_json["message_id"] == 108
        assert record_json["runner_id"] == "runner_camp5_1789128"
        assert record_json["provider"] == "WhatsAppWebProvider"
        assert record_json["operation"] == "send_message"
        assert record_json["result"] == "SUCCESS"
        assert record_json["error_code"] is None
        assert record_json["duration_ms"] == 3420.5
        assert record_json["details"]["phone"] == "+2010******78"
        assert record_json["details"]["attempts"] == 1

    def test_log_rotation_sizing(self, tmp_path):
        plain_log = tmp_path / "rot_test.log"

        # Initialize handler with tiny maxBytes for rotation test
        handler = SafeRotatingFileHandler(
            str(plain_log),
            maxBytes=200,
            backupCount=3,
            encoding="utf-8",
        )
        logger = logging.getLogger("rot_logger")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Write enough records to trigger rotation
        for i in range(25):
            logger.info(f"Writing log record number {i} to trigger rotation.")

        handler.flush()
        handler.close()

        # Check that backup files exist
        assert plain_log.exists()
        backup_1 = tmp_path / "rot_test.log.1"
        assert backup_1.exists()

    def test_safe_handler_does_not_crash_on_write_error(self, monkeypatch):
        handler = SafeRotatingFileHandler("dummy.log", maxBytes=1000, backupCount=1)
        record = logging.LogRecord("name", logging.INFO, "path", 1, "msg", (), None)

        def mock_emit_error(rec):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(logging.handlers.RotatingFileHandler, "emit", mock_emit_error)

        # Ensure emit does NOT raise or crash
        try:
            handler.emit(record)
        except Exception as e:
            pytest.fail(f"SafeRotatingFileHandler crashed on storage error: {e}")

    def test_safe_handler_rollover_errors_do_not_crash(self, monkeypatch):
        handler = SafeRotatingFileHandler("dummy.log", maxBytes=1000, backupCount=1)

        # 1. PermissionError during rollover
        def mock_perm_error():
            raise PermissionError("File in use on Windows")
        monkeypatch.setattr(logging.handlers.RotatingFileHandler, "doRollover", mock_perm_error)
        handler.doRollover()  # Should not raise

        # 2. Generic Exception during rollover
        def mock_gen_error():
            raise RuntimeError("Unexpected disk error")
        monkeypatch.setattr(logging.handlers.RotatingFileHandler, "doRollover", mock_gen_error)
        handler.doRollover()  # Should not raise

