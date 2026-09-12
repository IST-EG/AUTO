"""
Test fixtures for Web Control Center tests.
"""

import pytest
from starlette.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.web.dependencies import get_db
from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.web.app import app
from app.web.security.hasher import default_password_hasher
from app.web.security.brute_force import login_tracker


@pytest.fixture
def web_engine():
    """Isolated in-memory SQLite engine with StaticPool for multi-threaded testing."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def web_session(web_engine):
    """Provides an isolated database session."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=web_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def reset_login_tracker():
    """Resets brute force attempt state before each test."""
    with login_tracker._lock:
        login_tracker._records.clear()
    yield
    with login_tracker._lock:
        login_tracker._records.clear()


@pytest.fixture
def client(web_session):
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        yield web_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app, raise_server_exceptions=False)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def create_user(web_session):
    """Factory to create test users with specified role."""
    def _create(
        username: str = "testoperator",
        role: UserRole = UserRole.OPERATOR,
        password: str = "StrongPass123!@",
        is_active: bool = True
    ) -> User:
        user = User(
            username=username.strip().lower(),
            email=f"{username.strip().lower()}@example.com",
            password_hash=default_password_hasher.hash(password),
            role=role.value if isinstance(role, UserRole) else str(role),
            is_active=is_active
        )
        web_session.add(user)
        web_session.commit()
        web_session.refresh(user)
        return user

    return _create
