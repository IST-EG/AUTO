from unittest.mock import MagicMock, patch
import pytest

from app.providers.whatsapp_web.browser import WhatsAppBrowser
from app.providers.whatsapp_web.exceptions import (
    WhatsAppBrowserCrashError,
    WhatsAppNavigationError,
    WhatsAppSelectorError,
)


@pytest.fixture
def mock_driver():
    driver = MagicMock()
    driver.title = "WhatsApp"
    driver.current_url = "https://web.whatsapp.com"
    return driver


def test_browser_is_alive(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    assert wb.is_alive() is True

    # Driver raises exception
    from unittest.mock import PropertyMock
    type(mock_driver).title = PropertyMock(side_effect=Exception("Dead driver"))
    assert wb.is_alive() is False

    wb_no_driver = WhatsAppBrowser(driver=None)
    assert wb_no_driver.is_alive() is False


def test_browser_open_whatsapp(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    wb.open_whatsapp()
    mock_driver.get.assert_called_with("https://web.whatsapp.com")

    # When dead
    wb.driver = None
    with pytest.raises(WhatsAppBrowserCrashError):
        wb.open_whatsapp()


def test_browser_navigate_to_chat(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    wb.navigate_to_chat("+1 (415) 555-0199")
    mock_driver.get.assert_called_with("https://web.whatsapp.com/send?phone=14155550199")


def test_browser_is_invalid_phone_dialog(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)

    modal_element = MagicMock()
    modal_element.text = "Phone number shared via url is invalid."
    ok_btn = MagicMock()

    # Mock find_first_element
    def mock_find(selectors, timeout=None):
        if "popup-contents" in selectors[0]:
            return modal_element
        if "popup-controls-ok" in selectors[0]:
            return ok_btn
        return None

    wb.find_first_element = mock_find

    assert wb.is_invalid_phone_dialog_present() is True
    ok_btn.click.assert_called_once()


def test_browser_type_message(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    input_box = MagicMock()
    wb.find_first_element = MagicMock(return_value=input_box)

    wb.type_message("Hello World")
    input_box.click.assert_called_once()
    assert input_box.send_keys.call_count >= 2


def test_browser_click_send(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    send_btn = MagicMock()
    wb.find_first_element = MagicMock(return_value=send_btn)

    wb.click_send()
    send_btn.click.assert_called_once()


def test_browser_wait_for_send_confirmation(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)

    # Empty input box (text == "")
    input_box = MagicMock()
    input_box.text = "   "

    wb.find_first_element = MagicMock(return_value=input_box)
    wb.is_element_present = MagicMock(return_value=True)

    confirmed = wb.wait_for_send_confirmation(timeout=1.0)
    assert confirmed is True


def test_browser_diagnostic_snippet(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    snippet = wb.capture_diagnostic_snippet()
    assert "URL=https://web.whatsapp.com" in snippet
    assert "Title=WhatsApp" in snippet


def test_browser_quit(mock_driver):
    wb = WhatsAppBrowser(driver=mock_driver)
    wb.quit()
    mock_driver.quit.assert_called_once()
    assert wb.driver is None


def test_browser_start_default_without_custom_paths():
    with patch("os.makedirs"), \
         patch("selenium.webdriver.Chrome") as mock_chrome, \
         patch("selenium.webdriver.chrome.service.Service") as mock_service, \
         patch("app.providers.whatsapp_web.browser.settings") as mock_settings:
        mock_settings.WHATSAPP_CHROME_BINARY = ""
        mock_settings.WHATSAPP_CHROMEDRIVER_PATH = ""

        wb = WhatsAppBrowser(session_path="/dummy/path", headless=True)
        wb.start()

        mock_service.assert_not_called()
        mock_chrome.assert_called_once()
        call_kwargs = mock_chrome.call_args.kwargs
        assert "service" not in call_kwargs or call_kwargs.get("service") is None
        assert call_kwargs["options"].binary_location == ""


def test_browser_start_with_custom_paths_explicit():
    with patch("os.makedirs"), \
         patch("selenium.webdriver.Chrome") as mock_chrome, \
         patch("selenium.webdriver.chrome.service.Service") as mock_service:
        fake_service_instance = MagicMock()
        mock_service.return_value = fake_service_instance

        wb = WhatsAppBrowser(
            session_path="/dummy/path",
            headless=False,
            chrome_binary="/snap/chromium/current/usr/lib/chromium-browser/chrome",
            chromedriver_path="/usr/bin/chromedriver",
        )
        wb.start()

        mock_service.assert_called_once_with(executable_path="/usr/bin/chromedriver")
        mock_chrome.assert_called_once()
        call_kwargs = mock_chrome.call_args.kwargs
        assert call_kwargs["service"] == fake_service_instance
        assert call_kwargs["options"].binary_location == "/snap/chromium/current/usr/lib/chromium-browser/chrome"


def test_browser_start_with_settings_fallback():
    with patch("os.makedirs"), \
         patch("selenium.webdriver.Chrome") as mock_chrome, \
         patch("selenium.webdriver.chrome.service.Service") as mock_service, \
         patch("app.providers.whatsapp_web.browser.settings") as mock_settings:
        mock_settings.WHATSAPP_CHROME_BINARY = "/usr/bin/google-chrome"
        mock_settings.WHATSAPP_CHROMEDRIVER_PATH = "/usr/bin/chromedriver"

        wb = WhatsAppBrowser(session_path="/dummy/path")
        assert wb.chrome_binary == "/usr/bin/google-chrome"
        assert wb.chromedriver_path == "/usr/bin/chromedriver"

        wb.start()
        mock_service.assert_called_once_with(executable_path="/usr/bin/chromedriver")
        call_kwargs = mock_chrome.call_args.kwargs
        assert call_kwargs["options"].binary_location == "/usr/bin/google-chrome"
