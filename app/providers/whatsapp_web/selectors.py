"""
Centralized DOM Selectors for WhatsApp Web.

Provides multi-tier selector fallbacks (data-testid, aria-label, role, class)
to remain resilient against WhatsApp Web UI updates.
"""

from typing import List


class WhatsAppSelectors:
    """Centralized selector repository with prioritized fallback chains."""

    # 1. Authentication / QR
    QR_CANVAS: List[str] = [
        "canvas[aria-label*='Scan']",
        "canvas",
        "div[data-ref] canvas",
        "div[data-testid='qrcode'] canvas",
    ]

    QR_CONTAINER: List[str] = [
        "div[data-testid='qrcode']",
        "div[data-ref]",
        "div._ak96",
    ]

    # 2. Main Interface / Ready Indicators
    CHAT_LIST_PANE: List[str] = [
        "#pane-side",
        "div[data-testid='chat-list']",
        "div[aria-label='Chat list']",
        "div[role='region'][aria-label*='Chat']",
    ]

    SEARCH_INPUT: List[str] = [
        "div[data-testid='chat-list-search']",
        "div[contenteditable='true'][data-tab='3']",
        "p.selectable-text.copyable-text",
    ]

    # 3. Chat Window & Composing
    MESSAGE_INPUT: List[str] = [
        "div[data-testid='conversation-compose-box-input']",
        "footer div[contenteditable='true']",
        "div[aria-label='Type a message']",
        "div[data-tab='10']",
    ]

    SEND_BUTTON: List[str] = [
        "span[data-testid='send']",
        "button[aria-label='Send']",
        "button[data-testid='compose-btn-send']",
        "span[data-icon='send']",
    ]

    CONVERSATION_PANEL: List[str] = [
        "div[data-testid='conversation-panel-body']",
        "div[role='region'][aria-label='Message list']",
        "#main",
    ]

    # 4. Delivery & Confirmation Indicators
    OUTGOING_BUBBLE: List[str] = [
        "div.message-out",
        "div[data-testid='msg-container']",
        "div[data-id*='true_']",
    ]

    CONFIRMATION_CHECKMARKS: List[str] = [
        "span[data-testid='msg-check']",
        "span[data-testid='msg-dblcheck']",
        "span[data-testid='status-check']",
        "span[data-testid='status-dblcheck']",
        "span[data-icon='msg-check']",
        "span[data-icon='msg-dblcheck']",
        "span[data-icon='status-check']",
        "span[data-icon='status-dblcheck']",
        "span[data-icon='status-dblcheck-ack']",
        "span[aria-label*='Sent']",
        "span[aria-label*='Delivered']",
        "span[aria-label*='Read']",
        "span[aria-label*='تم إرسال']",
        "span[aria-label*='تم تسليم']",
        "span[aria-label*='تمت قراءة']",
        "span[data-testid='msg-time']",
        "span[data-icon='msg-time']",
        "span[data-testid='msg-meta']",
    ]

    # 5. Modals, Alerts, Banners
    INVALID_NUMBER_MODAL: List[str] = [
        "div[data-testid='popup-contents']",
        "div[data-animate-modal-popup='true']",
        "div[role='dialog']",
    ]

    INVALID_NUMBER_OK_BUTTON: List[str] = [
        "div[data-testid='popup-controls-ok']",
        "button[data-testid='popup-controls-ok']",
        "div[role='button'] span:contains('OK')",
    ]

    DISCONNECTED_BANNER: List[str] = [
        "div[data-testid='alert-phone-connecting']",
        "span[data-icon='alert-phone-connecting']",
        "div[data-testid='alert-computer-network-offline']",
    ]
