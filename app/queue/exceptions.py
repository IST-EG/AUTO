"""
Exceptions for queue operations.
"""

class QueueError(Exception):
    """Base exception for queue operations."""
    pass


class InvalidQueueStateTransitionError(QueueError):
    """Raised when an illegal transition is attempted on a queue item."""
    pass


class DuplicateMessageError(QueueError):
    """Raised when an idempotency conflict occurs."""
    pass


class MessageNotFoundError(QueueError):
    """Raised when a message cannot be located by ID or idempotency key."""
    pass


class MessageClaimError(QueueError):
    """Raised when claiming a message fails due to conflict or invalid state."""
    pass


class StaleLeaseError(QueueError):
    """Raised when operating on a message whose lease has expired."""
    pass
