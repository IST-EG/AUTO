"""
Queue domain package.
"""

from app.queue.state_machine import QueueState, QueueStateMachine
from app.queue.service import PersistentQueueService
from app.queue.exceptions import (
    QueueError,
    InvalidQueueStateTransitionError,
    DuplicateMessageError,
    MessageNotFoundError,
    MessageClaimError,
    StaleLeaseError,
)

__all__ = [
    "QueueState",
    "QueueStateMachine",
    "PersistentQueueService",
    "QueueError",
    "InvalidQueueStateTransitionError",
    "DuplicateMessageError",
    "MessageNotFoundError",
    "MessageClaimError",
    "StaleLeaseError",
]
