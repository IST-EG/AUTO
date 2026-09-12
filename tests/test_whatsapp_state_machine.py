import pytest
from app.providers.whatsapp_web.state import (
    WhatsAppSessionState,
    WhatsAppSessionStateMachine,
)
from app.providers.whatsapp_web.exceptions import WhatsAppSessionError


def test_whatsapp_session_valid_transitions():
    # DISCONNECTED -> AUTHENTICATING, CONNECTED, ERROR, STOPPED
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.DISCONNECTED, WhatsAppSessionState.AUTHENTICATING)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.DISCONNECTED, WhatsAppSessionState.CONNECTED)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.DISCONNECTED, WhatsAppSessionState.ERROR)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.DISCONNECTED, WhatsAppSessionState.STOPPED)

    # AUTHENTICATING -> CONNECTED, DISCONNECTED, ERROR, STOPPED
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.AUTHENTICATING, WhatsAppSessionState.CONNECTED)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.AUTHENTICATING, WhatsAppSessionState.DISCONNECTED)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.AUTHENTICATING, WhatsAppSessionState.ERROR)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.AUTHENTICATING, WhatsAppSessionState.STOPPED)

    # CONNECTED -> SESSION_LOST, DISCONNECTED, ERROR, STOPPED
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.CONNECTED, WhatsAppSessionState.SESSION_LOST)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.CONNECTED, WhatsAppSessionState.DISCONNECTED)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.CONNECTED, WhatsAppSessionState.ERROR)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.CONNECTED, WhatsAppSessionState.STOPPED)

    # SESSION_LOST -> AUTHENTICATING, DISCONNECTED
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.SESSION_LOST, WhatsAppSessionState.AUTHENTICATING)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.SESSION_LOST, WhatsAppSessionState.DISCONNECTED)

    # STOPPED -> DISCONNECTED (restart)
    assert WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.STOPPED, WhatsAppSessionState.DISCONNECTED)


def test_whatsapp_session_invalid_transitions():
    # Direct jump from DISCONNECTED to SESSION_LOST is invalid
    assert not WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.DISCONNECTED, WhatsAppSessionState.SESSION_LOST)

    # Direct jump from STOPPED to CONNECTED without restart is invalid
    assert not WhatsAppSessionStateMachine.can_transition(WhatsAppSessionState.STOPPED, WhatsAppSessionState.CONNECTED)

    with pytest.raises(WhatsAppSessionError) as exc:
        WhatsAppSessionStateMachine.validate_transition(WhatsAppSessionState.STOPPED, WhatsAppSessionState.CONNECTED)
    assert "Invalid session state transition" in str(exc.value)
