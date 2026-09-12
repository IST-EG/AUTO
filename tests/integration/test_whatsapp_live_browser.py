import os
import tempfile
import shutil
import pytest

from app.providers.whatsapp_web.browser import WhatsAppBrowser


@pytest.mark.live_browser
def test_live_browser_launch_and_quit():
    """
    Isolated live-browser integration test.
    Only executed when explicitly requested with: pytest -m live_browser
    """
    temp_dir = tempfile.mkdtemp()
    browser = None
    try:
        browser = WhatsAppBrowser(
            session_path=temp_dir,
            headless=True,
            browser_timeout=10,
            page_load_timeout=30
        )
        browser.start()
        assert browser.is_alive() is True

        # Open landing page
        browser.open_whatsapp()
        assert browser.is_alive() is True

        # Diagnostic capture
        diag = browser.capture_diagnostic_snippet()
        assert "URL=" in diag

    finally:
        if browser:
            browser.quit()
        shutil.rmtree(temp_dir, ignore_errors=True)
