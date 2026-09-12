from unittest.mock import MagicMock
import pytest

from app.providers.whatsapp_web.session_manager import WhatsAppSessionManager
from app.providers.whatsapp_web.state import WhatsAppSessionState
from app.providers.whatsapp_web.exceptions import WhatsAppSessionError


@pytest.fixture
def mock_browser():
    browser = MagicMock()
    browser.is_alive.return_value = True
    browser.is_chat_ready.return_value = False
    browser.is_qr_present.return_value = False
    return browser


def test_session_init_cached_profile(mock_browser):
    # Chat ready on launch -> immediately CONNECTED
    mock_browser.is_chat_ready.return_value = True
    sm = WhatsAppSessionManager(browser=mock_browser)

    state = sm.initialize_session()
    assert state == WhatsAppSessionState.CONNECTED
    assert sm.state == WhatsAppSessionState.CONNECTED
    mock_browser.open_whatsapp.assert_called_once()


def test_session_init_requires_qr(mock_browser):
    # Chat not ready, QR present -> AUTHENTICATING
    mock_browser.is_chat_ready.return_value = False
    mock_browser.is_qr_present.return_value = True
    sm = WhatsAppSessionManager(browser=mock_browser)

    state = sm.initialize_session()
    assert state == WhatsAppSessionState.AUTHENTICATING
    assert sm.state == WhatsAppSessionState.AUTHENTICATING

    # Now operator scans QR -> becomes CONNECTED
    mock_browser.is_chat_ready.return_value = True
    success = sm.await_authentication(timeout=2.0)
    assert success is True
    assert sm.state == WhatsAppSessionState.CONNECTED


def test_session_health_check(mock_browser):
    mock_browser.is_chat_ready.return_value = True
    sm = WhatsAppSessionManager(browser=mock_browser)
    sm.initialize_session()
    assert sm.state == WhatsAppSessionState.CONNECTED

    # Normal health check -> True
    assert sm.check_health() is True

    # Remote logout: QR canvas suddenly appears
    mock_browser.is_qr_present.return_value = True
    assert sm.check_health() is False
    assert sm.state == WhatsAppSessionState.SESSION_LOST

    # In SESSION_LOST, health check returns False
    assert sm.check_health() is False


def test_session_browser_crash(mock_browser):
    mock_browser.is_chat_ready.return_value = True
    sm = WhatsAppSessionManager(browser=mock_browser)
    sm.initialize_session()

    # Browser process dies
    mock_browser.is_alive.return_value = False
    assert sm.check_health() is False
    assert sm.state == WhatsAppSessionState.ERROR


def test_session_recovery_and_shutdown(mock_browser):
    mock_browser.is_chat_ready.return_value = True
    sm = WhatsAppSessionManager(browser=mock_browser)
    sm.initialize_session()

    # Restart session restores CONNECTED
    recovered = sm.restart_session()
    assert recovered is True
    assert sm.state == WhatsAppSessionState.CONNECTED

    # Clean shutdown
    sm.shutdown()
    assert sm.state == WhatsAppSessionState.STOPPED
    mock_browser.quit.assert_called()


def test_session_await_auth_edge_cases(mock_browser):
    sm = WhatsAppSessionManager(browser=mock_browser)
    # 1. State is DISCONNECTED -> raises error
    with pytest.raises(WhatsAppSessionError):
        sm.await_authentication()

    # 2. State is CONNECTED -> immediately returns True
    sm.state = WhatsAppSessionState.CONNECTED
    assert sm.await_authentication() is True

    # 3. State is AUTHENTICATING, times out -> returns False
    sm.state = WhatsAppSessionState.AUTHENTICATING
    mock_browser.is_chat_ready.return_value = False
    assert sm.await_authentication(timeout=0.1) is False


def test_session_health_edge_cases(mock_browser):
    sm = WhatsAppSessionManager(browser=mock_browser)
    # Not CONNECTED -> returns False
    assert sm.check_health() is False

    # CONNECTED but chat pane not ready
    sm.state = WhatsAppSessionState.CONNECTED
    mock_browser.is_alive.return_value = True
    mock_browser.is_qr_present.return_value = False
    mock_browser.is_chat_ready.return_value = False
    assert sm.check_health() is False


def test_session_restart_failure(mock_browser):
    sm = WhatsAppSessionManager(browser=mock_browser)
    # Browser open raises
    mock_browser.start.side_effect = Exception("Fatal Chrome error")
    recovered = sm.restart_session()
    assert recovered is False
    assert sm.state == WhatsAppSessionState.ERROR
