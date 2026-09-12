"""
Deterministic CLI Exit Codes for Outreach Automation.

Maps each failure category to a consistent integer exit code.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Deterministic CLI exit codes."""
    SUCCESS = 0
    GENERAL_ERROR = 1
    INVALID_ARGUMENT = 2
    NOT_FOUND = 3
    INVALID_STATE = 4
    AUTHENTICATION_REQUIRED = 5
    PROVIDER_UNAVAILABLE = 6
    EMERGENCY_STOP_ACTIVE = 7
    CIRCUIT_BREAKER_OPEN = 8
    CONCURRENCY_ERROR = 9
    UNKNOWN_OUTCOME_BLOCKED = 10
