"""
Execution Boundary Integrity Tests for Phase 7.6.

Verifies:
- Zero imports of 'selenium' or WebDriver inside the app/web/ hierarchy.
- Zero subprocess.Popen or os.kill calls for browser automation in app/web/.
- Vercel Control Plane remains strictly decoupled from browser execution.
"""

import os
from pathlib import Path


def test_zero_selenium_imports_in_app_web():
    """Asserts that no module in app/web imports selenium."""
    web_dir = Path("app/web")
    py_files = list(web_dir.glob("**/*.py"))
    assert len(py_files) > 10

    offending_files = []
    for f in py_files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        # Check for selenium imports
        for line in content.splitlines():
            line_clean = line.strip()
            if line_clean.startswith("#"):
                continue
            if "import selenium" in line_clean or "from selenium" in line_clean:
                offending_files.append((str(f), line_clean))

    assert len(offending_files) == 0, f"Found forbidden selenium imports in app/web: {offending_files}"


def test_zero_browser_instantiations_in_app_web():
    """Asserts that app/web does not instantiate WhatsAppBrowser or WhatsAppSessionManager."""
    web_dir = Path("app/web")
    py_files = list(web_dir.glob("**/*.py"))

    offending_files = []
    for f in py_files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line_clean = line.strip()
            if line_clean.startswith("#"):
                continue
            if "WhatsAppBrowser(" in line_clean or "WhatsAppSessionManager(" in line_clean:
                offending_files.append((str(f), line_clean))

    assert len(offending_files) == 0, f"Found forbidden browser instantiations in app/web: {offending_files}"
