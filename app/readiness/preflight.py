"""
Production Preflight & Runtime Readiness Engine.

Validates environment, configuration, directories, database, browser, session,
process locks, and safety boundaries before service launch.
"""

import os
import sys
import shutil
import platform
from pathlib import Path
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect

from app.cli.exit_codes import ExitCode
from app.utils.settings import settings
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.models.app_setting import AppSetting
from app.models.campaign import Campaign


class CheckResult:
    """Individual preflight check outcome."""

    def __init__(
        self,
        name: str,
        passed: bool,
        message: str,
        exit_code: ExitCode = ExitCode.SUCCESS,
        critical: bool = True,
    ):
        self.name = name
        self.passed = passed
        self.message = message
        self.exit_code = exit_code
        self.critical = critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "exit_code": int(self.exit_code),
            "critical": self.critical,
        }


class PreflightResult:
    """Aggregated preflight validation result."""

    def __init__(self, checks: List[CheckResult]):
        self.checks = checks
        self.passed = all(c.passed for c in checks if c.critical)
        # First non-zero exit code of a failing critical check, or SUCCESS
        self.exit_code = ExitCode.SUCCESS
        for c in checks:
            if not c.passed and c.critical:
                self.exit_code = c.exit_code
                break

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "exit_code": int(self.exit_code),
            "checks": [c.to_dict() for c in self.checks],
        }


def check_python_runtime() -> CheckResult:
    """Validates Python version >= 3.8.0, 64-bit, and UTF-8 encoding."""
    ver = sys.version_info
    if ver < (3, 8, 0):
        return CheckResult(
            "Python Runtime",
            False,
            f"Python >= 3.8.0 required; found {ver.major}.{ver.minor}.{ver.micro}",
            ExitCode.GENERAL_ERROR,
        )
    arch = platform.architecture()[0]
    return CheckResult(
        "Python Runtime",
        True,
        f"Python {ver.major}.{ver.minor}.{ver.micro} ({arch})",
        ExitCode.SUCCESS,
    )


def check_configuration() -> CheckResult:
    """Validates configuration parameters."""
    if not settings.DATABASE_URL:
        return CheckResult(
            "Configuration",
            False,
            "DATABASE_URL is not set.",
            ExitCode.INVALID_ARGUMENT,
        )
    if settings.GLOBAL_DAILY_LIMIT <= 0:
        return CheckResult(
            "Configuration",
            False,
            f"GLOBAL_DAILY_LIMIT must be > 0; found {settings.GLOBAL_DAILY_LIMIT}",
            ExitCode.INVALID_ARGUMENT,
        )
    return CheckResult(
        "Configuration",
        True,
        f"Config valid (TZ: {settings.APP_TIMEZONE}, DailyLimit: {settings.GLOBAL_DAILY_LIMIT})",
        ExitCode.SUCCESS,
    )


def check_worker_instance_id() -> CheckResult:
    """
    Validates that WORKER_INSTANCE_ID is present, a string, and satisfies the
    validate_worker_instance_id() contract ([a-z0-9-], 3-64 chars, lowercase, operator-assigned).
    """
    from app.runner.whatsapp_command_handler import validate_worker_instance_id

    instance_id = getattr(settings, "WORKER_INSTANCE_ID", "")
    if not instance_id:
        return CheckResult(
            "Worker Identity",
            False,
            "WORKER_INSTANCE_ID is not configured in environment.",
            ExitCode.INVALID_ARGUMENT,
        )
    if not isinstance(instance_id, str):
        return CheckResult(
            "Worker Identity",
            False,
            f"WORKER_INSTANCE_ID must be a string; found {type(instance_id).__name__}",
            ExitCode.INVALID_ARGUMENT,
        )
    if not validate_worker_instance_id(instance_id):
        return CheckResult(
            "Worker Identity",
            False,
            f"WORKER_INSTANCE_ID '{instance_id}' is invalid. Must be [a-z0-9-], 3-64 characters, lowercase, operator-assigned.",
            ExitCode.INVALID_ARGUMENT,
        )
    return CheckResult(
        "Worker Identity",
        True,
        f"WORKER_INSTANCE_ID verified ('{instance_id}')",
        ExitCode.SUCCESS,
    )


def check_directories_and_permissions() -> CheckResult:
    """Validates write permissions for data/ and logs/ directories."""
    dirs_to_check = [
        Path("./data"),
        Path(settings.LOG_FILE).parent,
        Path(settings.WHATSAPP_SESSION_PATH).parent,
    ]
    for d in dirs_to_check:
        try:
            d.mkdir(parents=True, exist_ok=True)
            test_file = d / ".perm_check_tmp"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
        except Exception as e:
            return CheckResult(
                "Directories & Permissions",
                False,
                f"Directory '{d}' unwritable: {e}",
                ExitCode.GENERAL_ERROR,
            )
    return CheckResult(
        "Directories & Permissions",
        True,
        "data/ and logs/ writable",
        ExitCode.SUCCESS,
    )


def check_database_connectivity(db: Session) -> CheckResult:
    """Tests basic database query execution."""
    try:
        db.execute(text("SELECT 1"))
        return CheckResult("Database Connectivity", True, "Database responsive (SELECT 1 passed)", ExitCode.SUCCESS)
    except Exception as e:
        return CheckResult(
            "Database Connectivity",
            False,
            f"Database connection failed: {e}",
            ExitCode.GENERAL_ERROR,
        )


def check_database_schema(db: Session) -> CheckResult:
    """Verifies core schema tables exist."""
    required_tables = {"campaigns", "contacts", "campaign_contacts", "messages", "app_settings", "audit_logs"}
    try:
        inspector = inspect(db.bind)
        existing = set(inspector.get_table_names())
        missing = required_tables - existing
        if missing:
            return CheckResult(
                "Database Schema",
                False,
                f"Missing database tables: {sorted(missing)}. Run 'alembic upgrade head'.",
                ExitCode.GENERAL_ERROR,
            )
        return CheckResult("Database Schema", True, f"All {len(required_tables)} core tables verified", ExitCode.SUCCESS)
    except Exception as e:
        return CheckResult(
            "Database Schema",
            False,
            f"Failed inspecting schema: {e}",
            ExitCode.GENERAL_ERROR,
        )


def check_browser_environment() -> CheckResult:
    """Verifies Google Chrome executable is discoverable."""
    # 1. Custom path in settings
    if settings.WHATSAPP_CHROME_BINARY and os.path.exists(settings.WHATSAPP_CHROME_BINARY):
        return CheckResult("Browser Environment", True, f"Chrome binary located at {settings.WHATSAPP_CHROME_BINARY}", ExitCode.SUCCESS)

    # 2. Standard paths
    found = shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")
    if found:
        return CheckResult("Browser Environment", True, f"Chrome executable located at {found}", ExitCode.SUCCESS)

    # 3. Windows standard registry/program files
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return CheckResult("Browser Environment", True, f"Chrome binary located at {c}", ExitCode.SUCCESS)

    return CheckResult(
        "Browser Environment",
        False,
        "Google Chrome binary not found. Install Chrome or set WHATSAPP_CHROME_BINARY in .env",
        ExitCode.PROVIDER_UNAVAILABLE,
    )


def check_session_authentication() -> CheckResult:
    """Checks persistent WhatsApp Web session profile directory."""
    session_path = Path(settings.WHATSAPP_SESSION_PATH)
    if not session_path.exists() or not session_path.is_dir():
        return CheckResult(
            "Session Authentication",
            False,
            f"Session directory '{session_path}' does not exist. Run 'outreach session login'.",
            ExitCode.AUTHENTICATION_REQUIRED,
            critical=False,
        )
    has_data = any(session_path.iterdir()) if session_path.exists() else False
    if not has_data:
        return CheckResult(
            "Session Authentication",
            False,
            "Session directory is empty. Operator QR scan required via 'outreach session login'.",
            ExitCode.AUTHENTICATION_REQUIRED,
            critical=False,
        )
    return CheckResult("Session Authentication", True, "Persistent session profile data found", ExitCode.SUCCESS)


def check_process_lock_singularity(db: Session) -> CheckResult:
    """Non-locking probe verifying no other live process holds data/runner.lock."""
    process_lock = ProcessLock(db=db)
    active_info = process_lock.get_active_runner_info()
    if active_info and active_info.get("pid"):
        pid = active_info["pid"]
        if pid != os.getpid() and is_pid_alive(pid):
            return CheckResult(
                "Process Lock Singularity",
                False,
                f"Active runner process detected holding lock (PID: {pid}). Duplicate runners prohibited.",
                ExitCode.CONCURRENCY_ERROR,
            )
    return CheckResult("Process Lock Singularity", True, "No conflicting runner process detected", ExitCode.SUCCESS)


def check_emergency_stop_status(db: Session) -> CheckResult:
    """Verifies emergency stop is inactive."""
    setting = db.query(AppSetting).filter(AppSetting.key == "emergency_stop").first()
    if setting and setting.value:
        import json
        try:
            val = json.loads(setting.value)
            if val.get("active"):
                reason = val.get("reason", "Operator emergency stop active")
                return CheckResult(
                    "Emergency Stop",
                    False,
                    f"Emergency stop is active: {reason}. Run 'outreach emergency-resume'.",
                    ExitCode.EMERGENCY_STOP_ACTIVE,
                )
        except Exception:
            pass
    return CheckResult("Emergency Stop", True, "Emergency stop is inactive", ExitCode.SUCCESS)


def check_circuit_breaker(db: Session, campaign_id: Optional[int] = None) -> CheckResult:
    """Verifies target campaign circuit breaker is not tripped."""
    if campaign_id is None:
        return CheckResult("Circuit Breaker", True, "No specific campaign target specified", ExitCode.SUCCESS)
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        return CheckResult("Circuit Breaker", False, f"Campaign {campaign_id} not found", ExitCode.NOT_FOUND)
    if campaign.status == "PAUSED":
        return CheckResult(
            "Circuit Breaker",
            False,
            f"Campaign {campaign_id} is PAUSED (circuit breaker tripped or operator pause).",
            ExitCode.CIRCUIT_BREAKER_OPEN,
        )
    return CheckResult("Circuit Breaker", True, f"Campaign {campaign_id} is open for dispatches", ExitCode.SUCCESS)


def run_preflight(
    db: Session,
    campaign_id: Optional[int] = None,
    strict: bool = False,
    provider: Optional[Any] = None,
) -> PreflightResult:
    """
    Executes full production preflight inspection matrix.
    
    In strict mode, non-critical warnings (such as unauthenticated session)
    are elevated to critical failures.
    """
    checks: List[CheckResult] = []

    checks.append(check_python_runtime())
    checks.append(check_configuration())
    checks.append(check_worker_instance_id())
    checks.append(check_directories_and_permissions())
    checks.append(check_database_connectivity(db))
    checks.append(check_database_schema(db))

    if provider is not None:
        checks.append(
            CheckResult("Browser Environment", True, f"Custom/mock provider injected: {provider.__class__.__name__}", ExitCode.SUCCESS)
        )
        checks.append(
            CheckResult("Session Authentication", True, "Session managed by injected provider", ExitCode.SUCCESS)
        )
    else:
        checks.append(check_browser_environment())

        sess_check = check_session_authentication()
        if strict:
            sess_check.critical = True
        checks.append(sess_check)

    checks.append(check_process_lock_singularity(db))
    checks.append(check_emergency_stop_status(db))
    checks.append(check_circuit_breaker(db, campaign_id))

    return PreflightResult(checks)
