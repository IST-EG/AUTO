"""
Signal Handling and Graceful Shutdown Coordinator.

Intercepts OS termination signals (SIGINT, SIGTERM) to coordinate
deterministic graceful shutdown at safe cancellation points.
"""

import signal
import logging
from typing import Callable, List

logger = logging.getLogger(__name__)


class SignalCoordinator:
    """
    Coordinates process shutdown flags upon receiving OS termination signals.
    """

    def __init__(self):
        self._shutdown_requested: bool = False
        self._callbacks: List[Callable[[], None]] = []
        self._original_sigint = None
        self._original_sigterm = None

    @property
    def shutdown_requested(self) -> bool:
        """Returns True if a termination signal has been intercepted."""
        return self._shutdown_requested

    def request_shutdown(self) -> None:
        """Manually triggers the shutdown flag (e.g. via CLI stop or fatal error)."""
        if not self._shutdown_requested:
            logger.info("Shutdown requested on signal coordinator.")
            self._shutdown_requested = True
            for callback in self._callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.warning(f"Error in shutdown callback: {e}")

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Registers a callback to execute upon shutdown request."""
        self._callbacks.append(callback)

    def register_handlers(self) -> None:
        """Registers handlers for SIGINT and SIGTERM."""
        try:
            self._original_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle_signal)
        except (ValueError, AttributeError) as e:
            logger.debug(f"Could not register SIGINT: {e}")

        try:
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError) as e:
            logger.debug(f"Could not register SIGTERM: {e}")

    def restore_handlers(self) -> None:
        """Restores original signal handlers."""
        if self._original_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._original_sigint)
            except (ValueError, AttributeError):
                pass
        if self._original_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, self._original_sigterm)
            except (ValueError, AttributeError):
                pass

    def _handle_signal(self, signum: int, frame) -> None:
        """Signal handler callback."""
        sig_name = "SIGINT" if signum == signal.SIGINT else ("SIGTERM" if signum == signal.SIGTERM else str(signum))
        logger.info(f"Caught signal {sig_name}. Initiating graceful shutdown at next safe point...")
        self.request_shutdown()
