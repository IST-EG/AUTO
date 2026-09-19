"""
Session Management CLI Commands.

Handles operator login, status checks, and logout for WhatsApp Web.
"""

import os
import shutil
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import print_header, print_card, print_success, print_warning_box, print_error
from app.models.audit_log import AuditLog
from app.utils.settings import settings

logger = logging.getLogger(__name__)


def handle_session_login(args, db: Session, session_manager=None) -> ExitCode:
    """
    Executes 'outreach session login'.
    Launches browser, detects authentication state, and awaits QR scan if needed.
    """
    print_header("WhatsApp Web Session Login", "Interactive Authentication")

    if session_manager is None:
        from app.providers.whatsapp_web.browser import WhatsAppBrowser
        from app.providers.whatsapp_web.session_manager import WhatsAppSessionManager

        browser = WhatsAppBrowser(
            session_path=settings.WHATSAPP_SESSION_PATH,
            headless=getattr(args, "headless", False),
            browser_timeout=settings.WHATSAPP_BROWSER_TIMEOUT,
            chrome_binary=settings.WHATSAPP_CHROME_BINARY or None,
            chromedriver_path=settings.WHATSAPP_CHROMEDRIVER_PATH or None,
        )
        session_manager = WhatsAppSessionManager(
            browser=browser,
            qr_timeout=getattr(args, "timeout", None) or settings.WHATSAPP_QR_TIMEOUT,
        )

    try:
        print("Initializing WhatsApp Web session...")
        state = session_manager.initialize_session()

        if state == "CONNECTED":
            print_success("Session is already authenticated from cached profile.")
            print_card("Session Status", {
                "State": "CONNECTED",
                "Profile Path": settings.WHATSAPP_SESSION_PATH,
                "Ready": "YES",
            })
            _log_session_event(db, "SESSION_LOGIN", status="CONNECTED", result="Reused cached profile")
            return ExitCode.SUCCESS

        print("Awaiting operator QR code scan in browser window...")
        timeout = getattr(args, "timeout", None) or settings.WHATSAPP_QR_TIMEOUT
        authenticated = session_manager.await_authentication(timeout=timeout)

        if authenticated:
            print_success("Authentication successful! Session is now connected.")
            _log_session_event(db, "SESSION_LOGIN", status="CONNECTED", result="QR scan authenticated")
            return ExitCode.SUCCESS
        else:
            print_warning_box(
                f"QR code scan timed out after {timeout} seconds.\n"
                "Please run 'outreach session login' again to scan the code.",
                title="AUTHENTICATION TIMEOUT"
            )
            _log_session_event(db, "SESSION_LOGIN", status="TIMED_OUT", error="QR scan timed out")
            return ExitCode.AUTHENTICATION_REQUIRED

    except Exception as e:
        print_error(f"Failed to complete session login: {e}")
        logger.error(f"Session login error: {e}", exc_info=True)
        _log_session_event(db, "SESSION_LOGIN", status="ERROR", error=str(e))
        return ExitCode.PROVIDER_UNAVAILABLE

    finally:
        try:
            session_manager.shutdown()
        except Exception:
            pass


def handle_session_status(args, db: Session, session_manager=None) -> ExitCode:
    """
    Executes 'outreach session status'.
    Displays persistent profile presence and active session health.
    """
    print_header("WhatsApp Web Session Status")

    session_path = settings.WHATSAPP_SESSION_PATH
    profile_exists = os.path.exists(session_path) and os.path.isdir(session_path)

    card_data = {
        "Profile Path": session_path,
        "Profile Exists": "YES" if profile_exists else "NO",
        "Headless Mode": "YES" if settings.WHATSAPP_HEADLESS else "NO",
        "Browser Timeout": f"{settings.WHATSAPP_BROWSER_TIMEOUT}s",
        "QR Timeout": f"{settings.WHATSAPP_QR_TIMEOUT}s",
    }

    if session_manager is not None:
        card_data["State"] = session_manager.state
        card_data["Health Check"] = "HEALTHY" if session_manager.check_health() else "UNHEALTHY"

    print_card("Active Session Details", card_data)
    return ExitCode.SUCCESS


def handle_session_logout(args, db: Session, session_manager=None) -> ExitCode:
    """
    Executes 'outreach session logout'.
    Performs controlled local session shutdown and optionally clears profile cache.
    """
    print_header("WhatsApp Web Session Logout")

    if session_manager:
        try:
            session_manager.shutdown()
        except Exception as e:
            logger.warning(f"Error shutting down active browser: {e}")

    clear_cache = getattr(args, "clear_cache", False)
    if clear_cache:
        session_path = settings.WHATSAPP_SESSION_PATH
        if os.path.exists(session_path):
            try:
                shutil.rmtree(session_path)
                print_success(f"Cleared persistent session cache at: {session_path}")
            except Exception as e:
                print_error(f"Could not remove session cache directory: {e}")
                return ExitCode.GENERAL_ERROR

    _log_session_event(db, "SESSION_LOGOUT", status="STOPPED", result=f"Logout completed (clear_cache={clear_cache})")
    print_success("Controlled local session shutdown completed.")
    return ExitCode.SUCCESS


def _log_session_event(db: Session, event_type: str, status: str, result: Optional[str] = None, error: Optional[str] = None):
    try:
        log = AuditLog(
            event_type=event_type,
            status=status,
            result=result,
            error_message=error
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.warning(f"Could not write audit log for session event: {e}")
        try:
            db.rollback()
        except Exception:
            pass
