import pytest
from app.models.campaign import Campaign
from app.scheduler.retry_manager import RetryManager
from app.scheduler.circuit_breaker import CircuitBreaker


def test_retry_manager_policy():
    rm = RetryManager(base_backoff_seconds=10.0, max_backoff_seconds=100.0, jitter_seconds=0.0)

    # Permanent failure: never retried
    should_retry, reason = rm.should_retry(is_temporary=False, current_attempts=1, max_attempts=3)
    assert should_retry is False
    assert reason == "PERMANENT_FAILURE"

    # Temporary failure with remaining attempts: retried
    should_retry, reason = rm.should_retry(is_temporary=True, current_attempts=1, max_attempts=3)
    assert should_retry is True
    assert reason == "TEMPORARY_RETRY_ELIGIBLE"

    # Temporary failure with attempts exhausted: not retried
    should_retry, reason = rm.should_retry(is_temporary=True, current_attempts=3, max_attempts=3)
    assert should_retry is False
    assert reason == "MAX_ATTEMPTS_EXHAUSTED"

    # Backoff progression
    assert rm.calculate_backoff_delay(attempt_count=1) == 10.0
    assert rm.calculate_backoff_delay(attempt_count=2) == 20.0
    assert rm.calculate_backoff_delay(attempt_count=3) == 40.0
    assert rm.calculate_backoff_delay(attempt_count=10) == 100.0  # capped at max


def test_circuit_breaker_tripping(db_session):
    camp = Campaign(name="CB Camp", message_template="Hi", error_threshold=3, status="RUNNING")
    db_session.add(camp)
    db_session.commit()

    cb = CircuitBreaker(db_session)

    # Failure 1
    tripped = cb.record_failure(camp, "Error 1")
    assert not tripped
    assert cb.get_consecutive_errors(camp.id) == 1
    assert camp.status == "RUNNING"

    # Success resets consecutive count
    cb.record_success(camp.id)
    assert cb.get_consecutive_errors(camp.id) == 0

    # Failure 1 & 2
    cb.record_failure(camp, "Error 1")
    cb.record_failure(camp, "Error 2")
    assert camp.status == "RUNNING"

    # Failure 3 trips circuit breaker
    tripped = cb.record_failure(camp, "Error 3")
    assert tripped is True
    db_session.refresh(camp)
    assert camp.status == "PAUSED"
