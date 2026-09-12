"""
WhatsApp Web Provider package.
"""

from app.providers.whatsapp_web.provider import WhatsAppWebProvider
from app.providers.whatsapp_web.session_manager import WhatsAppSessionManager
from app.providers.whatsapp_web.browser import WhatsAppBrowser
from app.providers.whatsapp_web.state import WhatsAppSessionState, WhatsAppSessionStateMachine
from app.providers.whatsapp_web.selectors import WhatsAppSelectors
from app.providers.whatsapp_web.error_mapper import WhatsAppErrorMapper, ErrorCategory, ErrorMappingResult
from app.providers.whatsapp_web.exceptions import (
    WhatsAppProviderError,
    WhatsAppSessionError,
    WhatsAppAuthenticationError,
    WhatsAppNavigationError,
    WhatsAppInvalidNumberError,
    WhatsAppSendTimeoutError,
    WhatsAppUnknownOutcomeError,
    WhatsAppBrowserCrashError,
    WhatsAppSelectorError,
)

__all__ = [
    "WhatsAppWebProvider",
    "WhatsAppSessionManager",
    "WhatsAppBrowser",
    "WhatsAppSessionState",
    "WhatsAppSessionStateMachine",
    "WhatsAppSelectors",
    "WhatsAppErrorMapper",
    "ErrorCategory",
    "ErrorMappingResult",
    "WhatsAppProviderError",
    "WhatsAppSessionError",
    "WhatsAppAuthenticationError",
    "WhatsAppNavigationError",
    "WhatsAppInvalidNumberError",
    "WhatsAppSendTimeoutError",
    "WhatsAppUnknownOutcomeError",
    "WhatsAppBrowserCrashError",
    "WhatsAppSelectorError",
]
