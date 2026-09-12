"""
Abstract MessageProvider interface and related types.

Decouples campaign logic and queue dispatching from any external messaging transport.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass(frozen=True)
class ProviderMessage:
    """Outbound message payload passed to a MessageProvider."""
    message_id: int
    idempotency_key: str
    recipient_phone: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SendResult:
    """Result returned by a MessageProvider following a send attempt."""
    success: bool
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    is_temporary_error: bool = False
    raw_response: Optional[Dict[str, Any]] = None


class MessageProvider(ABC):
    """
    Abstract messaging transport provider interface.
    All underlying delivery services (e.g. WhatsApp Web, Meta API, SMS) must implement this contract.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establishes provider connection/session."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Returns True if provider connection is healthy and ready to send."""
        pass

    @abstractmethod
    def send_message(self, message: ProviderMessage) -> SendResult:
        """
        Synchronously sends an outbound message to a recipient.
        Must return SendResult with temporary vs permanent failure classification.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully disconnects and releases provider resources."""
        pass
