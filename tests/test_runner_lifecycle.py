"""
Unit tests for runner lifecycle state machine and signal coordinator.
"""

import pytest
from app.runner.lifecycle import RunnerLifecycle, RunnerLifecycleState
from app.runner.signals import SignalCoordinator


def test_runner_lifecycle_transitions():
    lifecycle = RunnerLifecycle()
    assert lifecycle.current_state == RunnerLifecycleState.STOPPED
    assert lifecycle.is_running is False

    lifecycle.transition_to(RunnerLifecycleState.STARTING, "Starting process")
    assert lifecycle.current_state == RunnerLifecycleState.STARTING
    assert lifecycle.started_at is not None

    lifecycle.transition_to(RunnerLifecycleState.AUTHENTICATING, "Connecting to WhatsApp")
    assert lifecycle.current_state == RunnerLifecycleState.AUTHENTICATING

    lifecycle.transition_to(RunnerLifecycleState.RUNNING, "Ready to dispatch")
    assert lifecycle.current_state == RunnerLifecycleState.RUNNING
    assert lifecycle.is_running is True

    lifecycle.transition_to(RunnerLifecycleState.IDLE, "No messages queued")
    assert lifecycle.current_state == RunnerLifecycleState.IDLE
    assert lifecycle.is_running is True

    lifecycle.transition_to(RunnerLifecycleState.PAUSED, "Batch pause")
    assert lifecycle.current_state == RunnerLifecycleState.PAUSED
    assert lifecycle.is_running is False

    lifecycle.transition_to(RunnerLifecycleState.STOPPING, "Graceful termination")
    assert lifecycle.current_state == RunnerLifecycleState.STOPPING

    lifecycle.transition_to(RunnerLifecycleState.STOPPED, "Clean exit")
    assert lifecycle.current_state == RunnerLifecycleState.STOPPED


def test_signal_coordinator_manual_shutdown():
    coordinator = SignalCoordinator()
    assert coordinator.shutdown_requested is False

    callback_called = []
    coordinator.register_callback(lambda: callback_called.append(True))

    coordinator.request_shutdown()
    assert coordinator.shutdown_requested is True
    assert len(callback_called) == 1
