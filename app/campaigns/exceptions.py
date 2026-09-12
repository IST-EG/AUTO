"""
Exceptions for campaign management.
"""

class CampaignError(Exception):
    """Base exception for campaign errors."""
    pass

class InvalidStateTransitionError(CampaignError):
    """Raised when an invalid state transition is attempted."""
    pass

class TemplateValidationError(CampaignError):
    """Raised when a message template is invalid."""
    def __init__(self, message: str, errors: list = None):
        super().__init__(message)
        self.errors = errors or []

class CampaignNotFoundError(CampaignError):
    """Raised when a campaign cannot be found."""
    pass

class CampaignContactError(CampaignError):
    """Raised when there is an issue managing campaign contacts."""
    pass
