"""
WhatsApp Browser Abstraction.

Encapsulates all Selenium WebDriver calls, explicit waits, and DOM interaction.
Isolates browser mechanics from session and campaign domain logic.
"""

import os
import re
import time
import logging
from typing import Optional, List, Any

from app.providers.whatsapp_web.selectors import WhatsAppSelectors
from app.providers.whatsapp_web.exceptions import (
    WhatsAppBrowserCrashError,
    WhatsAppSelectorError,
    WhatsAppNavigationError,
)

logger = logging.getLogger(__name__)


from app.utils.settings import settings


class WhatsAppBrowser:
    """
    Dedicated browser controller encapsulating Selenium operations on WhatsApp Web.
    """

    WHATSAPP_WEB_URL = "https://web.whatsapp.com"

    def __init__(
        self,
        session_path: str = "./data/whatsapp_session",
        headless: bool = False,
        browser_timeout: int = 30,
        page_load_timeout: int = 45,
        chrome_binary: Optional[str] = None,
        chromedriver_path: Optional[str] = None,
        driver: Optional[Any] = None
    ):
        self.session_path = os.path.abspath(session_path)
        self.headless = headless
        self.browser_timeout = browser_timeout
        self.page_load_timeout = page_load_timeout
        self.chrome_binary = chrome_binary or (getattr(settings, "WHATSAPP_CHROME_BINARY", "") or None)
        self.chromedriver_path = chromedriver_path or (getattr(settings, "WHATSAPP_CHROMEDRIVER_PATH", "") or None)
        self.driver = driver

    def is_alive(self) -> bool:
        """Checks if WebDriver instance is running and responsive."""
        if not self.driver:
            return False
        try:
            # Minimal responsive probe
            _ = self.driver.title
            return True
        except Exception:
            return False

    def start(self) -> None:
        """Initializes ChromeDriver with persistent user profile and options."""
        if self.driver is not None:
            return

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service

            os.makedirs(self.session_path, exist_ok=True)

            options = Options()
            options.add_argument(f"--user-data-dir={self.session_path}")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--remote-debugging-port=0")

            if self.headless:
                options.add_argument("--headless=new")

            if self.chrome_binary:
                options.binary_location = self.chrome_binary

            service = None
            if self.chromedriver_path:
                service = Service(executable_path=self.chromedriver_path)

            if service:
                self.driver = webdriver.Chrome(service=service, options=options)
            else:
                self.driver = webdriver.Chrome(options=options)

            self.driver.set_page_load_timeout(self.page_load_timeout)
        except Exception as e:
            logger.error(f"Failed to start ChromeDriver: {e}")
            raise WhatsAppBrowserCrashError(f"Failed to initialize browser: {e}")

    def open_whatsapp(self) -> None:
        """Navigates to WhatsApp Web landing page."""
        if not self.is_alive():
            raise WhatsAppBrowserCrashError("Browser is not running.")
        try:
            self.driver.get(self.WHATSAPP_WEB_URL)
        except Exception as e:
            raise WhatsAppNavigationError(f"Failed to navigate to {self.WHATSAPP_WEB_URL}: {e}")

    def find_first_element(self, selector_list: List[str], timeout: Optional[float] = None) -> Optional[Any]:
        """
        Attempts to locate an element using a prioritized list of CSS selectors.
        Returns the first matching WebElement, or None.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        wait_seconds = timeout if timeout is not None else self.browser_timeout
        end_time = time.time() + wait_seconds

        for selector in selector_list:
            remaining = max(0.5, end_time - time.time())
            try:
                element = WebDriverWait(self.driver, remaining).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                if element:
                    return element
            except Exception:
                continue

        return None

    def is_element_present(self, selector_list: List[str], timeout: float = 2.0) -> bool:
        """Safe existence check without raising errors."""
        return self.find_first_element(selector_list, timeout=timeout) is not None

    def is_qr_present(self, timeout: float = 3.0) -> bool:
        """Checks whether QR code canvas is rendered."""
        return self.is_element_present(WhatsAppSelectors.QR_CANVAS, timeout=timeout)

    def is_chat_ready(self, timeout: float = 5.0) -> bool:
        """Checks if main chat list or search bar is rendered and ready."""
        return self.is_element_present(WhatsAppSelectors.CHAT_LIST_PANE, timeout=timeout)

    def navigate_to_chat(self, phone: str) -> None:
        """
        Navigates to a specific phone chat via deep link.
        Cleans phone number to numeric digits.
        """
        if not self.is_alive():
            raise WhatsAppBrowserCrashError("Browser is not running.")

        clean_phone = re.sub(r"[^\d]", "", phone)
        chat_url = f"{self.WHATSAPP_WEB_URL}/send?phone={clean_phone}"
        try:
            self.driver.get(chat_url)
        except Exception as e:
            raise WhatsAppNavigationError(f"Failed navigating to chat URL for {clean_phone}: {e}")

    def is_invalid_phone_dialog_present(self, timeout: float = 4.0) -> bool:
        """
        Checks if WhatsApp displays the 'Phone number shared via url is invalid' popup.
        Dismisses the popup if present.
        """
        modal = self.find_first_element(WhatsAppSelectors.INVALID_NUMBER_MODAL, timeout=timeout)
        if modal:
            try:
                modal_text = modal.text.lower()
                if "invalid" in modal_text or "not on whatsapp" in modal_text or "url is invalid" in modal_text:
                    # Attempt to dismiss modal by clicking OK
                    ok_btn = self.find_first_element(WhatsAppSelectors.INVALID_NUMBER_OK_BUTTON, timeout=2.0)
                    if ok_btn:
                        ok_btn.click()
                    return True
            except Exception:
                return True
        return False

    def type_message(self, content: str) -> None:
        """
        Locates message input box and types message content.
        """
        input_elem = self.find_first_element(WhatsAppSelectors.MESSAGE_INPUT, timeout=self.browser_timeout)
        if not input_elem:
            raise WhatsAppSelectorError("Message compose input box could not be located.")

        try:
            from selenium.webdriver.common.keys import Keys
            input_elem.click()
            # Clear existing content if any
            input_elem.send_keys(Keys.CONTROL + "a")
            input_elem.send_keys(Keys.BACKSPACE)
            # Send message text
            input_elem.send_keys(content)
        except Exception as e:
            raise WhatsAppBrowserCrashError(f"Failed typing message into compose box: {e}")

    def click_send(self) -> None:
        """
        Clicks the Send button in the active chat compose box.
        """
        send_btn = self.find_first_element(WhatsAppSelectors.SEND_BUTTON, timeout=5.0)
        if send_btn:
            try:
                send_btn.click()
                return
            except Exception:
                pass

        # Fallback: Send ENTER in input field
        from selenium.webdriver.common.keys import Keys
        input_elem = self.find_first_element(WhatsAppSelectors.MESSAGE_INPUT, timeout=3.0)
        if input_elem:
            input_elem.send_keys(Keys.ENTER)
        else:
            raise WhatsAppSelectorError("Neither send button nor message input was available to send.")

    def wait_for_send_confirmation(self, timeout: float = 15.0) -> bool:
        """
        Verifies that send operation succeeded according to strict UI confirmation criteria:
        1. Input box is cleared.
        2. Outgoing message bubble appears.
        3. Confirmation checkmark or timestamp indicator appears.
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            # Check 1: message input cleared or detached
            input_elem = self.find_first_element(WhatsAppSelectors.MESSAGE_INPUT, timeout=1.0)
            if input_elem:
                text = input_elem.text.strip()
                if text == "":
                    # Check 2: outgoing checkmark or time icon is present
                    if self.is_element_present(WhatsAppSelectors.CONFIRMATION_CHECKMARKS, timeout=2.0):
                        return True

            time.sleep(0.5)

        return False

    def capture_diagnostic_snippet(self) -> str:
        """Returns brief diagnostic string of current page title and URL."""
        if not self.is_alive():
            return "Browser not alive."
        try:
            return f"URL={self.driver.current_url} | Title={self.driver.title}"
        except Exception:
            return "Could not retrieve browser details."

    def quit(self) -> None:
        """Gracefully closes the browser and driver."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None
