"""
Queue State Machine.

Enforces valid transitions for queued messages.
Defines explicit semantics for PENDING vs QUEUED states.
"""

from typing import Set, Dict
from app.queue.exceptions import InvalidQueueStateTransitionError


class QueueState:
    """Explicit queue states."""
    PENDING = "PENDING"          # Message exists but is not yet eligible for worker claiming (e.g. waiting on schedule or batch start)
    QUEUED = "QUEUED"            # Message is active, validated, and ready for immediate worker claiming
    PROCESSING = "PROCESSING"    # Message has an active lease held by a worker
    SENT = "SENT"                # Successfully sent and confirmed (Terminal)
    FAILED = "FAILED"            # Permanently failed or max retries exhausted (Terminal)
    RETRY_PENDING = "RETRY_PENDING"  # Temporary failure; waiting until next_retry_at before becoming claimable again
    CANCELLED = "CANCELLED"      # Campaign cancelled or message aborted (Terminal)
    SKIPPED = "SKIPPED"          # Contact excluded/opted out prior to dispatch (Terminal)


class QueueStateMachine:
    """
    Validates and manages state transitions on Message queue records.
    """

    VALID_TRANSITIONS: Dict[str, Set[str]] = {
        QueueState.PENDING: {
            QueueState.QUEUED,
            QueueState.CANCELLED,
            QueueState.SKIPPED,
        },
        QueueState.QUEUED: {
            QueueState.PROCESSING,
            QueueState.CANCELLED,
            QueueState.SKIPPED,
        },
        QueueState.PROCESSING: {
            QueueState.SENT,
            QueueState.FAILED,
            QueueState.RETRY_PENDING,
            QueueState.SKIPPED,
        },
        QueueState.RETRY_PENDING: {
            QueueState.QUEUED,
            QueueState.PROCESSING,
            QueueState.CANCELLED,
            QueueState.FAILED,
        },
        QueueState.SENT: set(),
        QueueState.FAILED: set(),
        QueueState.SKIPPED: set(),
        QueueState.CANCELLED: set(),
    }

    TERMINAL_STATES: Set[str] = {
        QueueState.SENT,
        QueueState.FAILED,
        QueueState.SKIPPED,
        QueueState.CANCELLED,
    }

    @classmethod
    def can_transition(cls, current_state: str, target_state: str) -> bool:
        """Check if transition from current_state to target_state is allowed."""
        allowed = cls.VALID_TRANSITIONS.get(current_state, set())
        return target_state in allowed

    @classmethod
    def validate_transition(cls, current_state: str, target_state: str, message_id: int = None) -> None:
        """
        Validates transition. Raises InvalidQueueStateTransitionError if invalid.
        """
        if not cls.can_transition(current_state, target_state):
            msg_str = f" for message {message_id}" if message_id else ""
            raise InvalidQueueStateTransitionError(
                f"Invalid queue state transition{msg_str} from '{current_state}' to '{target_state}'."
            )

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        """Check if state is terminal."""
        return state in cls.TERMINAL_STATES
