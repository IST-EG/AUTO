"""
Web configuration settings for the Web Control Center.

Loads parameters from environment variables with safe, sensible production defaults.
"""

from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    """Configuration for Web Control Center ASGI application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    # Core Secrets & Host (supports both SECRET_KEY and WEB_SECRET_KEY)
    SECRET_KEY: Optional[str] = None
    WEB_SECRET_KEY: str = "integra_outreach_default_secret_key_change_in_prod_32chars!"
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8000

    # Environment & Debug (supports both APP_ENV and ENVIRONMENT)
    APP_ENV: Optional[str] = None
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    VERCEL: Optional[str] = None

    # Host Header Enforcement (comma-separated, '*' allows all)
    ALLOWED_HOSTS: str = "*"

    # Cookie & Session Configuration (supports both COOKIE_SECURE and WEB_COOKIE_SECURE)
    COOKIE_SECURE: Optional[bool] = None
    WEB_COOKIE_SECURE: bool = False  # Set to True in production (HTTPS)
    WEB_SESSION_COOKIE_NAME: str = "outreach_session_id"
    WEB_CSRF_COOKIE_NAME: str = "outreach_csrf_token"
    WEB_COOKIE_SAMESITE: str = "lax"
    WEB_SESSION_INACTIVITY_MINUTES: int = 30
    WEB_SESSION_ABSOLUTE_HOURS: int = 12
    WEB_MAX_CONCURRENT_SESSIONS: int = 2

    # Brute-force & Lockout Configuration
    WEB_MAX_FAILED_LOGINS: int = 5
    WEB_LOCKOUT_MINUTES: int = 15

    @model_validator(mode="after")
    def sync_aliases(self) -> "WebSettings":
        """Synchronizes legacy and standard cloud deployment variable names."""
        # 1. Synchronize APP_ENV and ENVIRONMENT
        if self.APP_ENV:
            self.ENVIRONMENT = self.APP_ENV
        elif self.ENVIRONMENT:
            self.APP_ENV = self.ENVIRONMENT

        # 2. Synchronize COOKIE_SECURE and WEB_COOKIE_SECURE
        if self.COOKIE_SECURE is not None:
            self.WEB_COOKIE_SECURE = self.COOKIE_SECURE
        elif self.ENVIRONMENT.lower() == "production":
            self.WEB_COOKIE_SECURE = True
            self.COOKIE_SECURE = True
        else:
            self.COOKIE_SECURE = self.WEB_COOKIE_SECURE

        # 3. Synchronize SECRET_KEY and WEB_SECRET_KEY
        if self.SECRET_KEY:
            self.WEB_SECRET_KEY = self.SECRET_KEY
        else:
            self.SECRET_KEY = self.WEB_SECRET_KEY

        return self


web_settings = WebSettings()
