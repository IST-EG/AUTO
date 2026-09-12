"""
Retry Manager.

Differentiates temporary technical failures from permanent failures.
Calculates deterministic exponential backoff with jitter.
"""

import random
from typing import Tuple


class RetryManager:
    """
    Manages retry policy, error classification, and backoff scheduling.
    """

    DEFAULT_BASE_BACKOFF_SECONDS = 15.0
    DEFAULT_MAX_BACKOFF_SECONDS = 300.0
    DEFAULT_JITTER_SECONDS = 5.0

    def __init__(
        self,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        jitter_seconds: float = DEFAULT_JITTER_SECONDS
    ):
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.jitter_seconds = jitter_seconds

    def should_retry(self, is_temporary: bool, current_attempts: int, max_attempts: int) -> Tuple[bool, str]:
        """
        Determines whether a failed message should be retried.
        Returns: (should_retry: bool, reason: str)
        """
        if not is_temporary:
            return False, "PERMANENT_FAILURE"

        if current_attempts >= max_attempts:
            return False, "MAX_ATTEMPTS_EXHAUSTED"

        return True, "TEMPORARY_RETRY_ELIGIBLE"

    def calculate_backoff_delay(self, attempt_count: int) -> float:
        """
        Calculates exponential backoff with randomized jitter.
        Formula: min(max_backoff, base * (2^(attempt - 1)) + uniform(0, jitter))
        """
        exponent = max(0, attempt_count - 1)
        raw_delay = self.base_backoff_seconds * (2 ** exponent)
        jitter = random.uniform(0, self.jitter_seconds)
        delay = min(self.max_backoff_seconds, raw_delay + jitter)
        return round(delay, 2)
