import pytest
from app.providers.whatsapp_web.error_mapper import (
    WhatsAppErrorMapper,
    ErrorCategory,
)
from app.providers.whatsapp_web.exceptions import (
    WhatsAppInvalidNumberError,
    WhatsAppSendTimeoutError,
    WhatsAppNavigationError,
    WhatsAppBrowserCrashError,
    WhatsAppSelectorError,
    WhatsAppUnknownOutcomeError,
)


def test_error_mapper_permanent_errors():
    # Invalid number
    res = WhatsAppErrorMapper.classify(
        exception=WhatsAppInvalidNumberError("Number not found")
    )
    assert res.category == ErrorCategory.PERMANENT
    assert res.is_temporary is False
    assert "not registered" in res.error_message

    # DOM error string indicating invalid number
    res_dom = WhatsAppErrorMapper.classify(dom_error="Phone number shared via url is invalid")
    assert res_dom.category == ErrorCategory.PERMANENT
    assert res_dom.is_temporary is False

    # Selector failure
    res_sel = WhatsAppErrorMapper.classify(
        exception=WhatsAppSelectorError("Message input box not found")
    )
    assert res_sel.category == ErrorCategory.PERMANENT
    assert res_sel.is_temporary is False


def test_error_mapper_temporary_errors():
    # Send timeout before send action was registered
    res_timeout = WhatsAppErrorMapper.classify(
        exception=WhatsAppSendTimeoutError("Checkmark timeout")
    )
    assert res_timeout.category == ErrorCategory.TEMPORARY
    assert res_timeout.is_temporary is True

    # Navigation error
    res_nav = WhatsAppErrorMapper.classify(
        exception=WhatsAppNavigationError("DNS lookup failed")
    )
    assert res_nav.category == ErrorCategory.TEMPORARY
    assert res_nav.is_temporary is True

    # Crash prior to send
    res_crash_presend = WhatsAppErrorMapper.classify(
        exception=WhatsAppBrowserCrashError("Connection refused"),
        send_action_attempted=False
    )
    assert res_crash_presend.category == ErrorCategory.TEMPORARY
    assert res_crash_presend.is_temporary is True


def test_error_mapper_unknown_outcome_after_send():
    """
    CRITICAL: When send action was attempted but confirmation was interrupted by crash/reload,
    category must be UNKNOWN_OUTCOME and is_temporary must be False to prevent blind duplicate delivery.
    """
    res_unknown_outcome = WhatsAppErrorMapper.classify(
        exception=WhatsAppBrowserCrashError("Chrome died mid-confirmation"),
        send_action_attempted=True
    )
    assert res_unknown_outcome.category == ErrorCategory.UNKNOWN_OUTCOME
    assert res_unknown_outcome.is_temporary is False
    assert "Manual review required" in res_unknown_outcome.error_message


def test_error_mapper_unknown_fallback():
    # Unrecognized exception
    res_unrecognized = WhatsAppErrorMapper.classify(
        exception=RuntimeError("Some bizarre browser state")
    )
    assert res_unrecognized.category == ErrorCategory.UNKNOWN
    # Must fail safely rather than blindly retrying
    assert res_unrecognized.is_temporary is False
