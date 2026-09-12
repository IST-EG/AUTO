"""
Regression tests for Alembic configuration and URL interpolation.

Verifies that DATABASE_URLs containing URL-encoded characters (such as %40, %23)
can pass through Alembic's ConfigParser interpolation without raising ValueError,
and that the resulting SQLAlchemy URL decodes the credentials accurately.
Also verifies that SQLite migration behavior remains completely unchanged.
"""

import os
import tempfile
import pytest
from alembic.config import Config
from alembic import command
from sqlalchemy.engine.url import make_url
from sqlalchemy import create_engine, inspect

from app.utils.settings import Settings


def test_alembic_config_interpolation_with_percent_encoded_password(monkeypatch):
    """
    Verifies that a DATABASE_URL with percent-encoded characters (%40, %23)
    does not trigger ConfigParser ValueError('invalid interpolation syntax').
    """
    synthetic_url = "postgresql://user:p%40ss%23word%24test@localhost:5432/testdb"
    monkeypatch.setenv("DATABASE_URL", synthetic_url)

    cfg = Config("alembic.ini")

    # Import env.py under the monkeypatched environment
    # In env.py: config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))
    s = Settings()
    assert s.DATABASE_URL == synthetic_url

    # Set using the exact logic from env.py
    cfg.set_main_option("sqlalchemy.url", s.DATABASE_URL.replace("%", "%%"))

    # ConfigParser get_main_option must return the exact original URL with single %
    retrieved_url = cfg.get_main_option("sqlalchemy.url")
    assert retrieved_url == synthetic_url

    # Section retrieval must also return the un-escaped URL
    section = cfg.get_section(cfg.config_ini_section)
    assert section["sqlalchemy.url"] == synthetic_url

    # SQLAlchemy must parse the decoded credentials correctly
    parsed = make_url(section["sqlalchemy.url"])
    assert parsed.username == "user"
    assert parsed.password == "p@ss#word$test"


def test_alembic_upgrade_sql_mode_with_percent_encoded_url(monkeypatch):
    """
    Verifies that 'alembic upgrade head --sql' succeeds when DATABASE_URL
    contains URL-encoded characters, proving env.py executes cleanly without error.
    """
    synthetic_url = "postgresql://postgres:p%40ss%23word@db.example.com:5432/postgres?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", synthetic_url)

    cfg = Config("alembic.ini")

    # Running offline upgrade in SQL mode triggers env.py:run_migrations_offline()
    # It must execute without raising ValueError
    command.upgrade(cfg, "head", sql=True)


def test_alembic_sqlite_migration_runtime_unchanged(tmp_path, monkeypatch):
    """
    Verifies that standard online SQLite migrations remain completely unchanged
    and apply cleanly from scratch to head.
    """
    db_path = tmp_path / "test_migration.db"
    sqlite_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", sqlite_url)

    cfg = Config("alembic.ini")

    # Run online migration to head on SQLite
    command.upgrade(cfg, "head")

    # Verify tables were created in the SQLite database
    engine = create_engine(sqlite_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    expected_tables = {
        "alembic_version",
        "app_settings",
        "campaigns",
        "contacts",
        "campaign_contacts",
        "messages",
        "send_sessions",
        "unsubscribes",
        "audit_logs",
        "campaign_batches",
        "users",
        "user_sessions",
        "message_templates",
        "message_template_versions",
    }

    assert expected_tables.issubset(set(tables)), f"Missing tables: {expected_tables - set(tables)}"
    engine.dispose()
