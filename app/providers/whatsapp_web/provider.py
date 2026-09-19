"""
WhatsApp Web Delivery Provider.

Implements the abstract MessageProvider interface using WhatsApp Web automation.
Encapsulates session management, browser interaction, and strict error classification.
"""

import time
import logging
from typing import Optional

from app.providers.base import MessageProvider, ProviderMessage, SendResult
from app.providers.whatsapp_web.browser import WhatsAppBrowser
from app.providers.whatsapp_web.session_manager import WhatsAppSessionManager
from app.providers.whatsapp_web.error_mapper import WhatsAppErrorMapper
from app.providers.whatsapp_web.exceptions import (
    WhatsAppInvalidNumberError,
    WhatsAppSendTimeoutError,
    WhatsAppBrowserCrashError,
    WhatsAppUnknownOutcomeError,
)

logger = logging.getLogger(__name__)


class WhatsAppWebProvider(MessageProvider):
    """
    MessageProvider implementation utilizing WhatsApp Web.
    """

    def __init__(
        self,
        session_manager: Optional[WhatsAppSessionManager] = None,
        session_path: str = "./data/whatsapp_session",
        headless: bool = False,
        browser_timeout: int = 30,
        qr_timeout: int = 120,
        send_confirmation_timeout: float = 15.0,
        chrome_binary: Optional[str] = None,
        chromedriver_path: Optional[str] = None,
    ):
        if session_manager is not None:
            self.session_manager = session_manager
            self.browser = session_manager.browser
        else:
            self.browser = WhatsAppBrowser(
                session_path=session_path,
                headless=headless,
                browser_timeout=browser_timeout,
                chrome_binary=chrome_binary,
                chromedriver_path=chromedriver_path,
            )
            self.session_manager = WhatsAppSessionManager(
                browser=self.browser,
                qr_timeout=qr_timeout
            )

        self.send_confirmation_timeout = send_confirmation_timeout

    def connect(self) -> None:
        """Establishes connection and awaits authentication if needed."""
        self.session_manager.initialize_session()
        if self.session_manager.state == "AUTHENTICATING":
            # Await user scan
            authenticated = self.session_manager.await_authentication()
            if not authenticated:
                logger.warning("WhatsApp Web authentication timed out.")

    def health_check(self) -> bool:
        """Returns True if session is authenticated and ready to send."""
        return self.session_manager.check_health()

    def send_message(self, message: ProviderMessage) -> SendResult:
        """
        Dispatches message through WhatsApp Web.
        Verifies send confirmation according to strict UI criteria.
        """
        if not self.health_check():
            return SendResult(
                success=False,
                error_message="WhatsApp provider is not connected or session is unhealthy.",
                is_temporary_error=True
            )

        send_action_attempted = False
        try:
            # 1. Direct navigate to chat
            self.browser.navigate_to_chat(message.recipient_phone)

            # 2. Check for invalid number dialog
            if self.browser.is_invalid_phone_dialog_present(timeout=4.0):
                raise WhatsAppInvalidNumberError(f"Phone number {message.recipient_phone} is not on WhatsApp.")

            # 3. Type content into compose box
            self.browser.type_message(message.content)

            # 4. Dispatch send action
            send_action_attempted = True
            self.browser.click_send()

            # 5. Await strict UI confirmation (SEND_CONFIRMED)
            confirmed = self.browser.wait_for_send_confirmation(timeout=self.send_confirmation_timeout)
            if not confirmed:
                raise WhatsAppSendTimeoutError(
                    f"Send confirmation checkmark timed out after {self.send_confirmation_timeout}s."
                )

            # Successfully confirmed
            return SendResult(
                success=True,
                provider_message_id=f"waw_{message.message_id}_{int(time.time())}"
            )

        except Exception as e:
            logger.error(f"Error during WhatsApp Web send: {e}")
            mapping = WhatsAppErrorMapper.classify(
                exception=e,
                send_action_attempted=send_action_attempted
            )
            return mapping.to_send_result(raw_details=self.browser.capture_diagnostic_snippet())

    def disconnect(self) -> None:
        """Cleanly halts provider and closes browser."""
        self.session_manager.shutdown()
