"""
WhatsApp Session State and State Machine.

Represents authentication and operational lifecycle states of WhatsApp Web.
"""

from typing import Dict, Set


class WhatsAppSessionState:
    """Explicit lifecycle states for WhatsApp Web session."""
    DISCONNECTED = "DISCONNECTED"      # Browser closed or uninitialized
    AUTHENTICATING = "AUTHENTICATING"  # QR code displayed, awaiting operator scan
    CONNECTED = "CONNECTED"            # Authenticated, chat interface responsive
    SESSION_LOST = "SESSION_LOST"      # Remote logout or connection severed
    ERROR = "ERROR"                    # Unrecoverable browser/driver failure
    STOPPED = "STOPPED"                # Explicitly halted by operator


class WhatsAppSessionStateMachine:
    """
    Validates transitions for WhatsApp Web session lifecycle.
    """

    VALID_TRANSITIONS: Dict[str, Set[str]] = {
        WhatsAppSessionState.DISCONNECTED: {
            WhatsAppSessionState.AUTHENTICATING,
            WhatsAppSessionState.CONNECTED,
            WhatsAppSessionState.ERROR,
            WhatsAppSessionState.STOPPED,
        },
        WhatsAppSessionState.AUTHENTICATING: {
            WhatsAppSessionState.CONNECTED,
            WhatsAppSessionState.DISCONNECTED,
            WhatsAppSessionState.ERROR,
            WhatsAppSessionState.STOPPED,
        },
        WhatsAppSessionState.CONNECTED: {
            WhatsAppSessionState.SESSION_LOST,
            WhatsAppSessionState.DISCONNECTED,
            WhatsAppSessionState.ERROR,
            WhatsAppSessionState.STOPPED,
        },
        WhatsAppSessionState.SESSION_LOST: {
            WhatsAppSessionState.AUTHENTICATING,
            WhatsAppSessionState.DISCONNECTED,
            WhatsAppSessionState.ERROR,
            WhatsAppSessionState.STOPPED,
        },
        WhatsAppSessionState.ERROR: {
            WhatsAppSessionState.DISCONNECTED,
            WhatsAppSessionState.AUTHENTICATING,
            WhatsAppSessionState.STOPPED,
        },
        WhatsAppSessionState.STOPPED: {
            WhatsAppSessionState.DISCONNECTED,
        },
    }

    @classmethod
    def can_transition(cls, current_state: str, next_state: str) -> bool:
        """Checks if transition is valid."""
        return next_state in cls.VALID_TRANSITIONS.get(current_state, set())

    @classmethod
    def validate_transition(cls, current_state: str, next_state: str) -> None:
        """Raises ValueError if transition is invalid."""
        if not cls.can_transition(current_state, next_state):
            from app.providers.whatsapp_web.exceptions import WhatsAppSessionError
            raise WhatsAppSessionError(
                f"Invalid session state transition from '{current_state}' to '{next_state}'."
            )
