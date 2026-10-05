"""
Phase 8 Step B: Session Activity Write Throttle Regression Test Suite.

Proves:
1. Activity within throttle window (< 300s) does NOT write to the database (last_active_at unchanged).
2. Activity after throttle window (>= 300s) DOES update last_active_at and commit to the database.
3. Revoked sessions remain rejected immediately (zero caching of revocation).
4. Expired sessions (absolute and inactivity timeout) remain rejected immediately.
5. Concurrent requests do not create incorrect session state.
6. Documents the security tradeoff: 300s throttle is acceptable because revocation remains uncached and authoritative.
"""

import time
import threading
from datetime import datetime, timezone, timedelta
import pytest

from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.web.config import web_settings
from app.web.security.session import SessionManager, hash_session_token, _ensure_utc


@pytest.fixture
def auth_setup(web_session, create_user):
    """Creates an active test user and session."""
    user = create_user(
        username="throttle_tester",
        password="SecurePassword123!#",
        role=UserRole.ADMIN,
        is_active=True
    )
    manager = SessionManager(
        inactivity_minutes=30,
        absolute_hours=12,
        max_concurrent=2,
        activity_throttle_seconds=300
    )
    raw_token = manager.create_session(web_session, user)
    return user, manager, raw_token


def test_activity_under_300s_does_not_write(web_session, auth_setup):
    """1. Activity < 300s does not update last_active_at in database."""
    user, manager, raw_token = auth_setup

    # Query baseline session record
    token_hash = hash_session_token(raw_token)
    session_rec = web_session.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()
    initial_last_active = session_rec.last_active_at

    # Simulate activity 60 seconds later (well within 300s throttle)
    # Perform validation via manager
    validated = manager.get_user_from_token(web_session, raw_token)
    assert validated is not None
    v_user, v_session = validated
    assert v_user.id == user.id

    # Refresh from database: last_active_at MUST NOT have changed
    web_session.refresh(session_rec)
    assert session_rec.last_active_at == initial_last_active


def test_activity_at_or_above_300s_updates_last_active(web_session, auth_setup):
    """2. Activity >= 300s updates last_active_at and commits to database."""
    user, manager, raw_token = auth_setup

    token_hash = hash_session_token(raw_token)
    session_rec = web_session.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()

    # Manually age last_active_at back by 305 seconds (exceeding 300s throttle)
    aged_time = datetime.now(timezone.utc) - timedelta(seconds=305)
    session_rec.last_active_at = aged_time
    web_session.commit()

    # Validation should now detect elapsed >= 300s and update last_active_at
    t_before = datetime.now(timezone.utc)
    validated = manager.get_user_from_token(web_session, raw_token)
    assert validated is not None

    web_session.refresh(session_rec)
    last_act = _ensure_utc(session_rec.last_active_at)
    # Check that session_rec.last_active_at was refreshed to ~now
    assert last_act >= t_before - timedelta(seconds=2)
    assert last_act > aged_time


def test_revoked_session_remains_rejected_immediately(web_session, auth_setup):
    """3. Revocation is uncached: invalidated session is rejected on the very next request."""
    user, manager, raw_token = auth_setup

    # Verify session is initially valid
    assert manager.get_user_from_token(web_session, raw_token) is not None

    # Invalidate session (logout / eviction)
    revoked = manager.invalidate_session(web_session, raw_token)
    assert revoked is True

    # Immediate next call MUST return None without any cached acceptance
    assert manager.get_user_from_token(web_session, raw_token) is None


def test_expired_session_remains_rejected(web_session, auth_setup):
    """4. Absolute expiration and inactivity expiration remain strictly enforced."""
    user, manager, raw_token = auth_setup
    token_hash = hash_session_token(raw_token)
    session_rec = web_session.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()

    # A. Absolute expiration
    session_rec.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    web_session.commit()
    assert manager.get_user_from_token(web_session, raw_token) is None

    # Recreate session for inactivity test
    new_token = manager.create_session(web_session, user)
    new_hash = hash_session_token(new_token)
    new_rec = web_session.query(UserSession).filter(UserSession.session_token_hash == new_hash).first()

    # B. Sliding inactivity timeout (> 30 min)
    new_rec.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    web_session.commit()
    assert manager.get_user_from_token(web_session, new_token) is None


def test_concurrent_requests_preserve_correct_session_state(tmp_path):
    """
    5. Concurrent requests hitting get_user_from_token do not corrupt session state
       or cause concurrency anomalies.
    """
    import os
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.database import Base

    db_path = str(tmp_path / "concurrent_session_test.db")
    engine = create_engine(
        f"sqlite:///{db_path}?timeout=30",
        connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)

    # Seed user and session
    setup_db = SessionFactory()
    user = User(
        username="concurrent_tester",
        email="conc@example.com",
        password_hash="fake_hash",
        role=UserRole.ADMIN,
        is_active=True
    )
    setup_db.add(user)
    setup_db.commit()

    manager = SessionManager(
        inactivity_minutes=30,
        absolute_hours=12,
        max_concurrent=2,
        activity_throttle_seconds=300
    )
    raw_token = manager.create_session(setup_db, user)

    token_hash = hash_session_token(raw_token)
    session_rec = setup_db.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()

    # Age session past throttle threshold so write is triggered
    aged_time = datetime.now(timezone.utc) - timedelta(seconds=350)
    session_rec.last_active_at = aged_time
    setup_db.commit()
    setup_db.close()

    results = []
    errors = []

    def worker_request():
        thread_db = SessionFactory()
        try:
            val = manager.get_user_from_token(thread_db, raw_token)
            results.append(val is not None)
        except Exception as e:
            errors.append(e)
        finally:
            thread_db.close()

    threads = [threading.Thread(target=worker_request) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert all(results)
    assert len(results) == 5

    # Verify session is still valid and updated in DB
    verify_db = SessionFactory()
    rec = verify_db.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()
    assert rec is not None
    last_act = _ensure_utc(rec.last_active_at)
    assert (datetime.now(timezone.utc) - last_act).total_seconds() < 10
    verify_db.close()
