"""
WhatsApp Provider Exceptions.
"""

class WhatsAppProviderError(Exception):
    """Base exception for WhatsApp Web provider errors."""
    pass


class WhatsAppSessionError(WhatsAppProviderError):
    """Raised on invalid session state or transition."""
    pass


class WhatsAppAuthenticationError(WhatsAppProviderError):
    """Raised when authentication fails or times out."""
    pass


class WhatsAppNavigationError(WhatsAppProviderError):
    """Raised when navigation to WhatsApp Web or a direct chat fails."""
    pass


class WhatsAppInvalidNumberError(WhatsAppProviderError):
    """Raised when WhatsApp Web reports the destination number is invalid."""
    pass


class WhatsAppSendTimeoutError(WhatsAppProviderError):
    """Raised when send confirmation checkmark times out."""
    pass


class WhatsAppUnknownOutcomeError(WhatsAppProviderError):
    """
    Raised when send action was executed but browser crashed/reloaded
    before send confirmation could be verified.
    Must NOT be blindly retried automatically.
    """
    pass


class WhatsAppBrowserCrashError(WhatsAppProviderError):
    """Raised when browser process dies or WebDriver disconnects unexpectedly."""
    pass


class WhatsAppSelectorError(WhatsAppProviderError):
    """Raised when a required UI element cannot be located by known selectors."""
    pass
