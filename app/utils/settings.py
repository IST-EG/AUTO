"""
Application settings loaded from environment variables using pydantic-settings.

This module provides a centralized Settings class that loads configuration from
the .env file and environment variables. The settings singleton can be imported
throughout the application.

Settings include:
  - DATABASE_URL: SQLite or PostgreSQL connection string
  - APP_TIMEZONE: IANA timezone for date boundaries and scheduling
  - LOG_LEVEL: Logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  - LOG_FILE: Path to the rotating log file
  - GLOBAL_DAILY_LIMIT: Maximum messages per calendar day across all campaigns
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration from .env file and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    APP_ENV: Optional[str] = None
    """Application deployment environment (e.g. production, development)."""

    DEBUG: bool = False
    """Enable or disable application debug mode."""

    VERCEL: Optional[str] = None
    """Indicator when running within Vercel serverless environment."""

    DATABASE_URL: str = "sqlite:///./data/whatsapp_outreach.db"
    """Database connection URL. Supports SQLite and PostgreSQL."""

    DB_POOL_SIZE: int = 5
    """SQLAlchemy connection pool size for PostgreSQL."""

    DB_MAX_OVERFLOW: int = 10
    """SQLAlchemy connection pool max overflow for PostgreSQL."""

    DB_POOL_RECYCLE: int = 1800
    """SQLAlchemy connection pool recycle time in seconds."""

    DB_POOL_PRE_PING: bool = True
    """Enable connection health pre-ping before checkout."""

    APP_TIMEZONE: str = "Africa/Cairo"
    """IANA timezone for date/time boundaries and campaign scheduling."""

    LOG_LEVEL: str = "INFO"
    """Root logger level: DEBUG, INFO, WARNING, ERROR, or CRITICAL."""

    LOG_TO_FILE: bool = True
    """Whether to write logs to rotating files (disable on serverless environments)."""

    LOG_FILE: str = "logs/app.log"
    """Path to the plaintext rotating file handler log output."""

    LOG_JSON_FILE: str = "logs/app.json.log"
    """Path to the structured JSON lines rotating file handler log output."""

    LOG_MAX_BYTES: int = 10485760
    """Maximum log file size in bytes before rotation (10 MB)."""

    LOG_BACKUP_COUNT: int = 10
    """Number of backup log files to retain after rotation."""

    GLOBAL_DAILY_LIMIT: int = 100
    """Maximum total messages sent across all campaigns per calendar day."""

    # WhatsApp Web Automation Settings (Phase 4)
    WHATSAPP_SESSION_PATH: str = "./data/whatsapp_session"
    """Filesystem path for persistent Chrome user profile."""

    WHATSAPP_HEADLESS: bool = False
    """Run Chrome in headless mode (False allows operator to scan QR code)."""

    WHATSAPP_BROWSER_TIMEOUT: int = 30
    """Element search wait timeout in seconds."""

    WHATSAPP_PAGE_LOAD_TIMEOUT: int = 45
    """Initial page load timeout in seconds."""

    WHATSAPP_QR_TIMEOUT: int = 120
    """Maximum seconds to await operator QR code authentication."""

    WHATSAPP_CHROME_BINARY: str = ""
    """Optional custom path to Google Chrome binary."""

    WHATSAPP_CHROMEDRIVER_PATH: str = ""
    """Optional custom path to chromedriver binary."""

    # Operational Runner & CLI Settings (Phase 5)
    RUNNER_POLL_INTERVAL_SECONDS: int = 5
    """Idle sleep time in seconds when queue has no claimable items."""

    RUNNER_HEARTBEAT_SECONDS: int = 15
    """Frequency of runner heartbeat writes to lockfile and database."""

    RUNNER_LOCK_FILE: str = "./data/runner.lock"
    """Filesystem path for production runner authoritative OS process lockfile."""

    CLI_PAGE_SIZE: int = 20
    """Default pagination limit for CLI status and inspect commands."""

    RUNNER_REMOTE_COORDINATION: bool = False
    """Enable database-mediated desired state coordination (automatic on Vercel/serverless)."""


# Singleton instance exported for use throughout the application
settings = Settings()
