"""
Production Worker Lifecycle State Manager.

Tracks in-process operational lifecycle states for the production runner daemon.
No database schema modifications are required; state is maintained in runtime memory
and exposed via process lock and audit logging.
"""

import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RunnerLifecycleState:
    """Operational lifecycle states of the production runner daemon."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    AUTHENTICATING = "AUTHENTICATING"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class RunnerLifecycle:
    """
    Tracks and manages in-process state transitions for the Production Runner.
    """

    VALID_TRANSITIONS = {
        RunnerLifecycleState.STOPPED: {RunnerLifecycleState.STARTING},
        RunnerLifecycleState.STARTING: {
            RunnerLifecycleState.AUTHENTICATING,
            RunnerLifecycleState.RUNNING,
            RunnerLifecycleState.FAILED,
            RunnerLifecycleState.STOPPING,
        },
        RunnerLifecycleState.AUTHENTICATING: {
            RunnerLifecycleState.RUNNING,
            RunnerLifecycleState.FAILED,
            RunnerLifecycleState.STOPPING,
        },
        RunnerLifecycleState.RUNNING: {
            RunnerLifecycleState.IDLE,
            RunnerLifecycleState.PAUSED,
            RunnerLifecycleState.STOPPING,
            RunnerLifecycleState.FAILED,
        },
        RunnerLifecycleState.IDLE: {
            RunnerLifecycleState.RUNNING,
            RunnerLifecycleState.PAUSED,
            RunnerLifecycleState.STOPPING,
            RunnerLifecycleState.FAILED,
        },
        RunnerLifecycleState.PAUSED: {
            RunnerLifecycleState.RUNNING,
            RunnerLifecycleState.STOPPING,
            RunnerLifecycleState.FAILED,
        },
        RunnerLifecycleState.STOPPING: {
            RunnerLifecycleState.STOPPED,
            RunnerLifecycleState.FAILED,
        },
        RunnerLifecycleState.FAILED: {
            RunnerLifecycleState.STOPPED,
        },
    }

    def __init__(self):
        self._state: str = RunnerLifecycleState.STOPPED
        self._history: List[Tuple[str, str, str]] = []  # (timestamp, state, reason)
        self._started_at: Optional[datetime] = None
        self._stopped_at: Optional[datetime] = None

    @property
    def current_state(self) -> str:
        """Returns the current lifecycle state."""
        return self._state

    @property
    def started_at(self) -> Optional[datetime]:
        """Returns startup timestamp."""
        return self._started_at

    @property
    def is_running(self) -> bool:
        """Returns True if the runner is in an active processing or idle state."""
        return self._state in (RunnerLifecycleState.RUNNING, RunnerLifecycleState.IDLE)

    def transition_to(self, new_state: str, reason: Optional[str] = None) -> None:
        """
        Transitions to a new lifecycle state with logging and history tracking.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        logger.info(f"Runner lifecycle transition: {self._state} -> {new_state} (Reason: {reason or 'Normal'})")

        if new_state == RunnerLifecycleState.STARTING and not self._started_at:
            self._started_at = datetime.now(timezone.utc)
        elif new_state in (RunnerLifecycleState.STOPPED, RunnerLifecycleState.FAILED):
            self._stopped_at = datetime.now(timezone.utc)

        self._history.append((now_str, new_state, reason or ""))
        self._state = new_state
