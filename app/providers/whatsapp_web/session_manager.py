"""
WhatsApp Session Manager.

Manages browser lifecycle, session persistence, QR authentication state,
session health monitoring, and recovery after disconnects.
"""

import time
import logging
from typing import Optional

from app.providers.whatsapp_web.state import (
    WhatsAppSessionState,
    WhatsAppSessionStateMachine,
)
from app.providers.whatsapp_web.browser import WhatsAppBrowser
from app.providers.whatsapp_web.exceptions import (
    WhatsAppSessionError,
    WhatsAppAuthenticationError,
    WhatsAppBrowserCrashError,
)

logger = logging.getLogger(__name__)


class WhatsAppSessionManager:
    """
    Controller for WhatsApp Web session lifecycle, authentication, and persistence.
    """

    def __init__(
        self,
        browser: WhatsAppBrowser,
        qr_timeout: int = 120
    ):
        self.browser = browser
        self.qr_timeout = qr_timeout
        self.state: str = WhatsAppSessionState.DISCONNECTED

    def _set_state(self, next_state: str) -> None:
        """Transitions state with validation."""
        WhatsAppSessionStateMachine.validate_transition(self.state, next_state)
        logger.info(f"WhatsApp session state transition: {self.state} -> {next_state}")
        self.state = next_state

    def initialize_session(self) -> str:
        """
        Launches browser and navigates to WhatsApp Web.
        Detects whether an existing persistent session is active or QR is required.
        """
        try:
            self.browser.start()
            self.browser.open_whatsapp()

            # Check if existing session cookies restored the chat interface
            if self.browser.is_chat_ready(timeout=8.0):
                self._set_state(WhatsAppSessionState.CONNECTED)
                return self.state

            # If QR canvas appears, transition to AUTHENTICATING
            if self.browser.is_qr_present(timeout=5.0):
                self._set_state(WhatsAppSessionState.AUTHENTICATING)
                return self.state

            # Fallback: assume authenticating if not immediately ready
            self._set_state(WhatsAppSessionState.AUTHENTICATING)
            return self.state
        except Exception as e:
            logger.error(f"Failed to initialize session: {e}")
            self.state = WhatsAppSessionState.ERROR
            raise WhatsAppSessionError(f"Session initialization failed: {e}")

    def await_authentication(self, timeout: Optional[float] = None) -> bool:
        """
        Awaits operator QR code scan.
        Transitions to CONNECTED upon detection of chat interface.
        """
        wait_seconds = timeout or self.qr_timeout
        end_time = time.time() + wait_seconds

        if self.state == WhatsAppSessionState.CONNECTED:
            return True

        if self.state != WhatsAppSessionState.AUTHENTICATING:
            raise WhatsAppSessionError(f"Cannot await authentication in state '{self.state}'.")

        logger.info(f"Awaiting operator QR code scan (timeout: {wait_seconds}s)...")
        while time.time() < end_time:
            if self.browser.is_chat_ready(timeout=2.0):
                self._set_state(WhatsAppSessionState.CONNECTED)
                logger.info("WhatsApp Web session authenticated successfully.")
                return True

            time.sleep(1.0)

        logger.warning("QR code scan timed out.")
        return False

    def check_health(self) -> bool:
        """
        Verifies browser responsiveness and active login state.
        Detects session loss or unexpected logouts.
        """
        if self.state != WhatsAppSessionState.CONNECTED:
            return False

        if not self.browser.is_alive():
            logger.warning("Browser driver is not responsive.")
            self._set_state(WhatsAppSessionState.ERROR)
            return False

        # If QR canvas reappeared, session was lost/logged out remotely
        if self.browser.is_qr_present(timeout=1.0):
            logger.warning("QR code detected while CONNECTED. Session was lost remotely.")
            self._set_state(WhatsAppSessionState.SESSION_LOST)
            return False

        # Verify chat pane remains visible
        if not self.browser.is_chat_ready(timeout=2.0):
            logger.warning("Chat list pane not visible during health check.")
            return False

        return True

    def restart_session(self) -> bool:
        """
        Recovers after browser crash or session disruption.
        Restarts browser using the same user-data-dir without wiping credentials.
        """
        logger.info("Attempting session recovery / browser restart...")
        try:
            self.browser.quit()
        except Exception:
            pass

        self.state = WhatsAppSessionState.DISCONNECTED
        try:
            self.initialize_session()
            if self.state == WhatsAppSessionState.CONNECTED:
                logger.info("Session restored automatically from cached profile.")
                return True
            return False
        except Exception as e:
            logger.error(f"Session recovery failed: {e}")
            self.state = WhatsAppSessionState.ERROR
            return False

    def shutdown(self) -> None:
        """Gracefully shuts down browser session."""
        try:
            self._set_state(WhatsAppSessionState.STOPPED)
        except Exception:
            self.state = WhatsAppSessionState.STOPPED
        self.browser.quit()
