"""
Production structured logging configuration with handler-safe sanitization and rotation.

Provides:
  - configure_logging(): Idempotent root logger setup with dual rotating files and console
  - get_logger(name): Named logger factory
  - mask_phone(phone): E.164-aware and Egyptian phone number masking
  - sanitize_text(text): Scrub sensitive credentials, tokens, cookies, and mask phone numbers
  - sanitize_dict(data): Scrub dictionaries, strictly omitting message bodies
  - log_event(...): Structured event emitter supporting the Phase 6 taxonomy
"""

import datetime
import errno
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Optional

from app.utils.settings import settings

# ---------------------------------------------------------------------------
# Redaction & Masking Patterns
# ---------------------------------------------------------------------------

# Sensitive keywords: api-key, secret, token, password, auth, cookie, session
SENSITIVE_KV_PATTERN = re.compile(
    r"(?i)(api[-_]?key|secret|token|password|auth|credential|cookie|session_id|session_token)([\s:=]+)([^\s,;\"']+)",
    re.UNICODE,
)

# Raw JWT / Bearer tokens
JWT_TOKEN_PATTERN = re.compile(
    r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]+",
    re.UNICODE,
)

# International E.164 numbers (starts with + followed by 7-15 digits, allowing punctuation)
E164_PHONE_PATTERN = re.compile(
    r"(?<!\w)(\+\d{1,3}[\d\s\-\(\)\.]{6,16}\d)(?!\w)",
    re.UNICODE,
)

# Egyptian national numbers without country code (010, 011, 012, 015 + 8 digits)
EGYPT_LOCAL_PHONE_PATTERN = re.compile(
    r"(?<!\d)(01[0125][\s\-\.]?\d{4}[\s\-\.]?\d{4})(?!\d)",
    re.UNICODE,
)

# Fields that MUST NEVER be logged (outbound message bodies)
OMITTED_BODY_FIELDS = {
    "message_content",
    "body",
    "rendered_content",
    "message_body",
    "raw_body",
    "content",
}


def mask_phone(phone: Optional[str]) -> str:
    """
    Mask a phone number for privacy preservation.
    
    Supports:
      - Egyptian E.164 numbers: +201012345678 -> +2010******78
      - Egyptian local numbers: 01012345678 -> 010******78
      - International E.164 numbers: +14155552671 -> +1415*****71, +447911123456 -> +4479******56
      - Formatted numbers (+20 101 234 5678) -> normalized & masked
      - Already-masked values (+2010******78) -> preserved idempotently without double-masking
    """
    if not phone:
        return "N/A"
    
    phone_str = str(phone).strip()
    if not phone_str:
        return "N/A"
        
    # Idempotent: if already masked with asterisks, do not re-mask
    if "*" in phone_str:
        return phone_str
        
    # Clean non-digit characters except leading '+'
    digits_only = re.sub(r"[^\d+]", "", phone_str)
    
    # E.164 format: + followed by digits
    if digits_only.startswith("+") and len(digits_only) >= 8:
        # Preserve first 5 chars (+ country code + initial network prefix), last 2 digits, mask middle
        prefix = digits_only[:5]
        suffix = digits_only[-2:]
        num_asterisks = max(4, len(digits_only) - 7)
        return prefix + ("*" * num_asterisks) + suffix
        
    # Egyptian local mobile format (010, 011, 012, 015 + 8 digits = 11 digits)
    if (digits_only.startswith("010") or digits_only.startswith("011") or 
        digits_only.startswith("012") or digits_only.startswith("015")) and len(digits_only) == 11:
        return digits_only[:3] + ("*" * 6) + digits_only[-2:]
        
    # Generic numbers >= 7 digits
    if len(digits_only) >= 7:
        prefix_len = min(3, len(digits_only) - 4)
        suffix_len = 2
        num_asterisks = len(digits_only) - prefix_len - suffix_len
        return digits_only[:prefix_len] + ("*" * num_asterisks) + digits_only[-suffix_len:]
        
    return phone_str


def sanitize_text(text: str) -> str:
    """
    Scrub credentials, tokens, cookies, and mask phone numbers in raw text.
    
    Does not mutate underlying records and operates idempotently.
    """
    if not text:
        return text
        
    # 1. Scrub credentials and tokens
    sanitized = SENSITIVE_KV_PATTERN.sub(r"\1\2[REDACTED]", text)
    sanitized = JWT_TOKEN_PATTERN.sub("[REDACTED]", sanitized)
    
    # 2. Mask international E.164 phone numbers
    def _mask_e164_match(match: re.Match) -> str:
        raw_val = match.group(0)
        # Avoid masking dates like 2026-09-11
        if raw_val.startswith("+"):
            return mask_phone(raw_val)
        return raw_val

    sanitized = E164_PHONE_PATTERN.sub(_mask_e164_match, sanitized)
    
    # 3. Mask Egyptian local phone numbers
    def _mask_eg_match(match: re.Match) -> str:
        return mask_phone(match.group(0))
        
    sanitized = EGYPT_LOCAL_PHONE_PATTERN.sub(_mask_eg_match, sanitized)
    
    return sanitized


def sanitize_dict(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Sanitize dictionary key-values, masking PII and strictly omitting message bodies.
    """
    if not data or not isinstance(data, dict):
        return data
        
    sanitized: Dict[str, Any] = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        
        # Rule 1: Outbound message bodies must NOT be logged by default
        if key_lower in OMITTED_BODY_FIELDS:
            content_len = len(str(value)) if value is not None else 0
            sanitized[key] = f"<content_length: {content_len}>"
            continue
            
        # Rule 2: Credentials and secrets
        if any(term in key_lower for term in ("token", "secret", "password", "key", "auth", "cookie", "credential")):
            sanitized[key] = "[REDACTED]"
            continue
            
        # Rule 3: Phone number fields
        if any(term in key_lower for term in ("phone", "recipient", "mobile", "contact_number")):
            sanitized[key] = mask_phone(str(value)) if value is not None else None
            continue
            
        # Rule 4: Recursive structures
        if isinstance(value, dict):
            sanitized[key] = sanitize_dict(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_dict(item) if isinstance(item, dict)
                else sanitize_text(str(item)) if isinstance(item, str)
                else item
                for item in value
            ]
        elif isinstance(value, str):
            sanitized[key] = sanitize_text(value)
        else:
            sanitized[key] = value
            
    return sanitized


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class SanitizingTextFormatter(logging.Formatter):
    """
    Human-readable log formatter that guarantees sanitization of all output.
    
    Format: %(asctime)s | %(levelname)-8s | %(name)s | %(message)s
    """
    def format(self, record: logging.LogRecord) -> str:
        formatted_message = super().format(record)
        return sanitize_text(formatted_message)


class SanitizingJsonLinesFormatter(logging.Formatter):
    """
    Deterministic machine-readable JSON lines formatter adhering to the Phase 6 contract.
    """
    def format(self, record: logging.LogRecord) -> str:
        # Standardized UTC ISO-8601 timestamp with milliseconds
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        timestamp_str = now_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        # Build structured dictionary per Phase 6 specification
        event_dict: Dict[str, Any] = {
            "timestamp": timestamp_str,
            "level": record.levelname,
            "event": getattr(record, "event", record.levelname),
            "component": getattr(record, "component", record.name.split(".")[0]),
            "message": sanitize_text(record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", None),
            "campaign_id": getattr(record, "campaign_id", None),
            "campaign_contact_id": getattr(record, "campaign_contact_id", None),
            "message_id": getattr(record, "message_id", None),
            "runner_id": getattr(record, "runner_id", None),
            "provider": getattr(record, "provider", None),
            "operation": getattr(record, "operation", None),
            "result": getattr(record, "result", None),
            "error_code": getattr(record, "error_code", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }

        # Optional sanitized details
        raw_details = getattr(record, "details", None)
        if raw_details is not None:
            event_dict["details"] = sanitize_dict(raw_details)
        else:
            event_dict["details"] = None

        return json.dumps(event_dict, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Handlers with Non-Crashing Storage Failure Semantics
# ---------------------------------------------------------------------------

class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    RotatingFileHandler with non-crashing resilience against storage errors:
      - Disk full / ENOSPC
      - Unwritable / missing directories
      - Windows file locking contention during rollover (PermissionError)
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except (OSError, IOError) as err:
            # Fallback to stderr without crashing runner loop
            try:
                msg = self.format(record)
                sys.stderr.write(f"[LOG_FALLBACK_STDERR] Failed writing log ({err}): {msg}\n")
                sys.stderr.flush()
            except Exception:
                pass
        except Exception:
            self.handleError(record)

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # On Windows, rollover can raise PermissionError if a backup or indexer
            # holds the file momentarily. Keep appending to current file without crashing.
            sys.stderr.write("[LOG_WARN] Log rollover deferred due to Windows file lock contention.\n")
        except Exception as err:
            sys.stderr.write(f"[LOG_WARN] Log rollover failed ({err}); continuing write.\n")


# ---------------------------------------------------------------------------
# Setup & Logger Factory
# ---------------------------------------------------------------------------

_logging_configured: bool = False


def configure_logging(
    log_file: Optional[str] = None,
    json_log_file: Optional[str] = None,
    level: Optional[str] = None,
    force_reconfigure: bool = False,
) -> None:
    """
    Configure root logging with dual-destination rotating handlers.
    
    Guarantees:
      - Single emission per logical event
      - Strictly idempotent (zero duplicate handlers)
      - Independent handler sanitization (child logger safe)
      - 10MB max size (10,485,760 bytes), 10 backups
    """
    global _logging_configured

    root_logger = logging.getLogger()

    if force_reconfigure:
        for h in list(root_logger.handlers):
            if isinstance(h, SafeRotatingFileHandler):
                root_logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        _logging_configured = False

    # Idempotent check: do not add duplicate handlers
    if any(isinstance(h, SafeRotatingFileHandler) for h in root_logger.handlers):
        return

    # Determine paths and levels
    target_log_file = log_file or getattr(settings, "LOG_FILE", "logs/app.log")
    target_json_file = json_log_file or getattr(settings, "LOG_JSON_FILE", "logs/app.json.log")
    target_level = level or getattr(settings, "LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, target_level.upper(), logging.INFO)

    root_logger.setLevel(numeric_level)

    text_formatter = SanitizingTextFormatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    # Console stdout handler (Always enabled and sanitized)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(text_formatter)
        console_handler.setLevel(numeric_level)
        root_logger.addHandler(console_handler)

    # Check if rotating file handlers should be enabled (disabled on Vercel / serverless)
    is_vercel = bool(os.environ.get("VERCEL"))
    enable_file_logging = getattr(settings, "LOG_TO_FILE", True) and not is_vercel

    if enable_file_logging:
        try:
            # Ensure parent log directory exists safely
            log_dir = Path(target_log_file).parent
            log_dir.mkdir(parents=True, exist_ok=True)

            max_bytes = getattr(settings, "LOG_MAX_BYTES", 10485760)
            backup_count = getattr(settings, "LOG_BACKUP_COUNT", 10)

            # Destination 1: Plaintext app.log
            text_handler = SafeRotatingFileHandler(
                target_log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            text_handler.setFormatter(text_formatter)
            text_handler.setLevel(numeric_level)
            root_logger.addHandler(text_handler)

            # Destination 2: Structured JSON Lines app.json.log
            json_formatter = SanitizingJsonLinesFormatter()
            json_handler = SafeRotatingFileHandler(
                target_json_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            json_handler.setFormatter(json_formatter)
            json_handler.setLevel(numeric_level)
            root_logger.addHandler(json_handler)
        except Exception as err:
            sys.stderr.write(f"[LOG_WARN] File logging disabled due to filesystem constraint: {err}\n")

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger for a module.
    """
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    component: str,
    message: str,
    correlation_id: Optional[str] = None,
    campaign_id: Optional[int] = None,
    campaign_contact_id: Optional[int] = None,
    message_id: Optional[int] = None,
    runner_id: Optional[str] = None,
    provider: Optional[str] = None,
    operation: Optional[str] = None,
    result: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_ms: Optional[float] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Emit a structured operational event through the standard logging pipeline.
    
    Both plain text and JSON handlers receive this record with identical sanitized content.
    """
    extra_fields = {
        "event": event,
        "component": component,
        "correlation_id": correlation_id,
        "campaign_id": campaign_id,
        "campaign_contact_id": campaign_contact_id,
        "message_id": message_id,
        "runner_id": runner_id,
        "provider": provider,
        "operation": operation,
        "result": result,
        "error_code": error_code,
        "duration_ms": duration_ms,
        "details": details,
    }
    logger.log(level, message, extra=extra_fields)
