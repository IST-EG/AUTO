"""
Database connection and session management.

This module provides:
  - engine: SQLAlchemy engine configured with SQLite/PostgreSQL settings
  - Base: SQLAlchemy declarative base for all ORM models
  - SessionLocal: sessionmaker factory for creating database sessions
  - get_db(): Context manager for dependency injection of database sessions

The engine is configured with:
  - WAL mode and foreign key constraints enabled for SQLite
  - Connection pooling and thread safety
  - Proper UTF-8 encoding

All models must inherit from Base and be imported into app/models/__init__.py
for Alembic autodiscovery to work correctly.
"""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import NullPool

from app.utils.settings import settings

# Configure engine arguments based on database dialect
_connect_args = {}
_engine_kwargs = {
    "echo": False,
}

if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    _engine_kwargs["connect_args"] = _connect_args
else:
    # PostgreSQL configuration for Supabase / PgBouncer
    # Supabase Transaction Pooler (Port 6543) Compatibility Guarantees:
    # 1. psycopg2 does NOT use server-side prepared statements (binds parameters client-side via %s).
    # 2. SQLAlchemy transaction boundaries use standard COMMIT/ROLLBACK without session state leakage.
    # 3. No session-level SET statements or temp tables are utilized.
    if os.getenv("VERCEL"):
        # In serverless runtimes, NullPool prevents frozen lambdas from exhausting pooler connection slots.
        _engine_kwargs["poolclass"] = NullPool
    else:
        # Dedicated Worker VPS connection pooling
        _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
        _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        _engine_kwargs["pool_recycle"] = settings.DB_POOL_RECYCLE
        _engine_kwargs["pool_pre_ping"] = settings.DB_POOL_PRE_PING

engine = create_engine(
    settings.DATABASE_URL,
    **_engine_kwargs
)


# Enable WAL mode and foreign keys for every new SQLite connection
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """
    Configure SQLite connection with WAL mode and foreign key constraints.
    Applied strictly when using the SQLite dialect.
    """
    if engine.dialect.name == "sqlite":
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Declarative base for all ORM models
Base = declarative_base()

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    Context manager for dependency injection of database sessions.

    Usage:
        with get_db() as db:
            contact = db.query(Contact).first()

    Yields:
        A SQLAlchemy Session instance.

    Ensures:
        - Session is properly closed even if an exception occurs.
        - Rollback on exception to maintain transaction integrity.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
