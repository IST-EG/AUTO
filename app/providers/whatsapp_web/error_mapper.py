"""
WhatsApp Error Mapper.

Defines the contract mapping browser/Selenium exceptions and DOM states to:
TEMPORARY, PERMANENT, UNKNOWN, or UNKNOWN_OUTCOME.
"""

from dataclasses import dataclass
from typing import Optional

from app.providers.base import SendResult
from app.providers.whatsapp_web.exceptions import (
    WhatsAppInvalidNumberError,
    WhatsAppSendTimeoutError,
    WhatsAppUnknownOutcomeError,
    WhatsAppBrowserCrashError,
    WhatsAppNavigationError,
    WhatsAppSelectorError,
    WhatsAppAuthenticationError,
)


class ErrorCategory:
    PERMANENT = "PERMANENT"
    TEMPORARY = "TEMPORARY"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ErrorMappingResult:
    category: str
    error_message: str
    is_temporary: bool

    def to_send_result(self, raw_details: Optional[str] = None) -> SendResult:
        """Converts error mapping into standard SendResult."""
        return SendResult(
            success=False,
            error_message=f"[{self.category}] {self.error_message}",
            is_temporary_error=self.is_temporary,
            raw_response={"category": self.category, "details": raw_details} if raw_details else None
        )


class WhatsAppErrorMapper:
    """
    Translates raw browser exceptions, timeouts, and DOM states into standardized outcomes.
    """

    @classmethod
    def classify(
        cls,
        exception: Optional[Exception] = None,
        dom_error: Optional[str] = None,
        send_action_attempted: bool = False
    ) -> ErrorMappingResult:
        """
        Classifies error into PERMANENT, TEMPORARY, UNKNOWN_OUTCOME, or UNKNOWN.
        """
        # 1. Check for UNKNOWN_OUTCOME
        # If click_send() was executed but a crash, reload, or disconnection interrupted confirmation
        if send_action_attempted and isinstance(exception, (WhatsAppBrowserCrashError, WhatsAppUnknownOutcomeError)):
            return ErrorMappingResult(
                category=ErrorCategory.UNKNOWN_OUTCOME,
                error_message="Send action may have occurred but confirmation was interrupted by browser crash/reload. Manual review required.",
                is_temporary=False  # Do not blindly retry automatically
            )

        # 2. Permanent errors
        if isinstance(exception, WhatsAppInvalidNumberError) or (dom_error and "invalid" in dom_error.lower()):
            return ErrorMappingResult(
                category=ErrorCategory.PERMANENT,
                error_message="Destination phone number is not registered on WhatsApp or invalid.",
                is_temporary=False
            )

        if isinstance(exception, WhatsAppSelectorError):
            return ErrorMappingResult(
                category=ErrorCategory.PERMANENT,
                error_message=f"UI element selector failure: {str(exception)}",
                is_temporary=False
            )

        # 3. Temporary technical errors
        if isinstance(exception, WhatsAppSendTimeoutError):
            return ErrorMappingResult(
                category=ErrorCategory.TEMPORARY,
                error_message="Timed out waiting for send confirmation checkmark in WhatsApp Web.",
                is_temporary=True
            )

        if isinstance(exception, (WhatsAppNavigationError, WhatsAppBrowserCrashError)):
            return ErrorMappingResult(
                category=ErrorCategory.TEMPORARY,
                error_message=f"Browser navigation or connectivity failure: {str(exception)}",
                is_temporary=True
            )

        if isinstance(exception, WhatsAppAuthenticationError):
            return ErrorMappingResult(
                category=ErrorCategory.TEMPORARY,
                error_message=f"Session authentication error: {str(exception)}",
                is_temporary=True
            )

        # 4. Fallback unknown error
        exc_str = str(exception) if exception else (dom_error or "Unrecognized error state")
        return ErrorMappingResult(
            category=ErrorCategory.UNKNOWN,
            error_message=f"Unknown browser error: {exc_str}",
            is_temporary=False  # Unknown errors fail safely rather than entering retry loops
        )
