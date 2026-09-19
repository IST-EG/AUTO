from unittest.mock import MagicMock
import pytest

from app.providers.base import ProviderMessage
from app.providers.whatsapp_web.provider import WhatsAppWebProvider
from app.providers.whatsapp_web.state import WhatsAppSessionState
from app.providers.whatsapp_web.exceptions import WhatsAppBrowserCrashError


@pytest.fixture
def mock_session_manager():
    sm = MagicMock()
    sm.state = WhatsAppSessionState.CONNECTED
    sm.check_health.return_value = True
    sm.browser = MagicMock()
    sm.browser.is_alive.return_value = True
    sm.browser.is_invalid_phone_dialog_present.return_value = False
    sm.browser.wait_for_send_confirmation.return_value = True
    sm.browser.capture_diagnostic_snippet.return_value = "Test snippet"
    return sm


def test_provider_health_check_failure(mock_session_manager):
    mock_session_manager.check_health.return_value = False
    provider = WhatsAppWebProvider(session_manager=mock_session_manager)

    assert provider.health_check() is False

    msg = ProviderMessage(1, "k1", "+14155552671", "Hello")
    result = provider.send_message(msg)
    assert result.success is False
    assert result.is_temporary_error is True
    assert "not connected" in result.error_message


def test_provider_successful_send_confirmed(mock_session_manager):
    provider = WhatsAppWebProvider(session_manager=mock_session_manager)
    msg = ProviderMessage(10, "k10", "+14155552672", "Hello there")

    result = provider.send_message(msg)
    assert result.success is True
    assert result.provider_message_id is not None
    assert "waw_10_" in result.provider_message_id

    # Verify flow
    mock_session_manager.browser.navigate_to_chat.assert_called_with("+14155552672")
    mock_session_manager.browser.type_message.assert_called_with("Hello there")
    mock_session_manager.browser.click_send.assert_called_once()
    mock_session_manager.browser.wait_for_send_confirmation.assert_called_once()


def test_provider_invalid_number_dialog(mock_session_manager):
    mock_session_manager.browser.is_invalid_phone_dialog_present.return_value = True
    provider = WhatsAppWebProvider(session_manager=mock_session_manager)

    msg = ProviderMessage(11, "k11", "+19999999999", "Hello")
    result = provider.send_message(msg)

    assert result.success is False
    assert result.is_temporary_error is False
    assert "[PERMANENT]" in result.error_message
    assert "not registered" in result.error_message


def test_provider_send_confirmation_timeout(mock_session_manager):
    mock_session_manager.browser.wait_for_send_confirmation.return_value = False
    provider = WhatsAppWebProvider(session_manager=mock_session_manager)

    msg = ProviderMessage(12, "k12", "+14155552673", "Hello")
    result = provider.send_message(msg)

    assert result.success is False
    assert result.is_temporary_error is True
    assert "[TEMPORARY]" in result.error_message
    assert "Timed out waiting" in result.error_message


def test_provider_unknown_outcome_crash_after_send(mock_session_manager):
    # Simulate crash during wait_for_send_confirmation (after send action was performed)
    mock_session_manager.browser.wait_for_send_confirmation.side_effect = WhatsAppBrowserCrashError("Browser killed")
    provider = WhatsAppWebProvider(session_manager=mock_session_manager)

    msg = ProviderMessage(13, "k13", "+14155552674", "Hello")
    result = provider.send_message(msg)

    assert result.success is False
    # CRITICAL: Must not be marked as temporary retry to prevent potential duplicate external delivery
    assert result.is_temporary_error is False
    assert "[UNKNOWN_OUTCOME]" in result.error_message
    assert "Manual review required" in result.error_message


def test_provider_disconnect(mock_session_manager):
    provider = WhatsAppWebProvider(session_manager=mock_session_manager)
    provider.disconnect()
    mock_session_manager.shutdown.assert_called_once()


def test_provider_connect_flow(mock_session_manager):
    mock_session_manager.state = "AUTHENTICATING"
    mock_session_manager.await_authentication.return_value = True

    provider = WhatsAppWebProvider(session_manager=mock_session_manager)
    provider.connect()

    mock_session_manager.initialize_session.assert_called_once()
    mock_session_manager.await_authentication.assert_called_once()


def test_provider_propagates_custom_paths():
    provider = WhatsAppWebProvider(
        chrome_binary="/opt/custom/chrome",
        chromedriver_path="/opt/custom/chromedriver",
    )
    assert provider.browser.chrome_binary == "/opt/custom/chrome"
    assert provider.browser.chromedriver_path == "/opt/custom/chromedriver"
