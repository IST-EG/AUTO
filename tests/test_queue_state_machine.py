import pytest
from app.queue.state_machine import QueueState, QueueStateMachine
from app.queue.exceptions import InvalidQueueStateTransitionError


def test_queue_state_valid_transitions():
    # PENDING -> QUEUED, CANCELLED, SKIPPED
    assert QueueStateMachine.can_transition(QueueState.PENDING, QueueState.QUEUED)
    assert QueueStateMachine.can_transition(QueueState.PENDING, QueueState.CANCELLED)
    assert QueueStateMachine.can_transition(QueueState.PENDING, QueueState.SKIPPED)

    # QUEUED -> PROCESSING, CANCELLED, SKIPPED
    assert QueueStateMachine.can_transition(QueueState.QUEUED, QueueState.PROCESSING)
    assert QueueStateMachine.can_transition(QueueState.QUEUED, QueueState.CANCELLED)
    assert QueueStateMachine.can_transition(QueueState.QUEUED, QueueState.SKIPPED)

    # PROCESSING -> SENT, FAILED, RETRY_PENDING, SKIPPED
    assert QueueStateMachine.can_transition(QueueState.PROCESSING, QueueState.SENT)
    assert QueueStateMachine.can_transition(QueueState.PROCESSING, QueueState.FAILED)
    assert QueueStateMachine.can_transition(QueueState.PROCESSING, QueueState.RETRY_PENDING)
    assert QueueStateMachine.can_transition(QueueState.PROCESSING, QueueState.SKIPPED)

    # RETRY_PENDING -> QUEUED, PROCESSING, CANCELLED, FAILED
    assert QueueStateMachine.can_transition(QueueState.RETRY_PENDING, QueueState.QUEUED)
    assert QueueStateMachine.can_transition(QueueState.RETRY_PENDING, QueueState.PROCESSING)
    assert QueueStateMachine.can_transition(QueueState.RETRY_PENDING, QueueState.CANCELLED)
    assert QueueStateMachine.can_transition(QueueState.RETRY_PENDING, QueueState.FAILED)


def test_queue_state_invalid_transitions():
    # Direct jump from PENDING to PROCESSING or SENT is forbidden
    assert not QueueStateMachine.can_transition(QueueState.PENDING, QueueState.PROCESSING)
    assert not QueueStateMachine.can_transition(QueueState.PENDING, QueueState.SENT)

    # Direct jump from QUEUED to SENT is forbidden (must be claimed into PROCESSING first)
    assert not QueueStateMachine.can_transition(QueueState.QUEUED, QueueState.SENT)

    # Terminal states allow no further transitions
    for terminal_state in [QueueState.SENT, QueueState.FAILED, QueueState.SKIPPED, QueueState.CANCELLED]:
        assert QueueStateMachine.is_terminal(terminal_state)
        for any_state in [QueueState.PENDING, QueueState.QUEUED, QueueState.PROCESSING, QueueState.SENT]:
            assert not QueueStateMachine.can_transition(terminal_state, any_state)


def test_validate_transition_raises():
    with pytest.raises(InvalidQueueStateTransitionError) as exc:
        QueueStateMachine.validate_transition(QueueState.SENT, QueueState.QUEUED, message_id=42)
    assert "Invalid queue state transition for message 42" in str(exc.value)
