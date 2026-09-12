"""
MockMessageProvider.

Deterministic mock implementation of MessageProvider for tests and verification.
Supports simulated temporary, permanent, and per-recipient failures without network access.
"""

import time
from typing import List, Dict, Optional, Tuple, Any

from app.providers.base import MessageProvider, ProviderMessage, SendResult


class MockMessageProvider(MessageProvider):
    """
    Mock implementation of MessageProvider for deterministic testing.
    """

    def __init__(
        self,
        default_success: bool = True,
        default_temporary_error: bool = False,
        default_error_message: str = "Simulated provider failure",
        simulated_latency: float = 0.0
    ):
        self.default_success = default_success
        self.default_temporary_error = default_temporary_error
        self.default_error_message = default_error_message
        self.simulated_latency = simulated_latency

        self.is_connected = False
        self.sent_messages: List[ProviderMessage] = []
        # Map: recipient_phone -> (success: bool, is_temporary: bool, error_msg: Optional[str])
        self.phone_behaviors: Dict[str, Tuple[bool, bool, Optional[str]]] = {}

    def configure_recipient_behavior(self, phone: str, success: bool, is_temporary: bool = False, error_msg: Optional[str] = None) -> None:
        """Configures custom outcome for a specific phone number."""
        self.phone_behaviors[phone] = (success, is_temporary, error_msg)

    def connect(self) -> None:
        """Connects the mock provider."""
        self.is_connected = True

    def health_check(self) -> bool:
        """Returns True if connected."""
        return self.is_connected

    def send_message(self, message: ProviderMessage) -> SendResult:
        """Sends a message according to configured rules."""
        if not self.is_connected:
            return SendResult(
                success=False,
                error_message="Provider is not connected",
                is_temporary_error=True
            )

        if self.simulated_latency > 0:
            time.sleep(self.simulated_latency)

        # Check phone-specific behavior
        if message.recipient_phone in self.phone_behaviors:
            success, is_temp, error_msg = self.phone_behaviors[message.recipient_phone]
            if success:
                self.sent_messages.append(message)
                return SendResult(
                    success=True,
                    provider_message_id=f"mock_msg_{len(self.sent_messages)}_{message.message_id}"
                )
            else:
                return SendResult(
                    success=False,
                    error_message=error_msg or self.default_error_message,
                    is_temporary_error=is_temp
                )

        # Default behavior
        if self.default_success:
            self.sent_messages.append(message)
            return SendResult(
                success=True,
                provider_message_id=f"mock_msg_{len(self.sent_messages)}_{message.message_id}"
            )
        else:
            return SendResult(
                success=False,
                error_message=self.default_error_message,
                is_temporary_error=self.default_temporary_error
            )

    def disconnect(self) -> None:
        """Disconnects the mock provider."""
        self.is_connected = False
