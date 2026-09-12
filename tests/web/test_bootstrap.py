"""
Tests for First-Run OWNER Bootstrap.

Verifies:
- Setup availability with zero users vs existing users
- Valid bootstrap account creation and audit trail
- Rejection of weak passwords
- Duplicate bootstrap prevention
- Multi-threaded concurrent bootstrap safety (SQLite-safe)
- Zero default credentials
"""

import os
import tempfile
import concurrent.futures
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog
from app.web.services.bootstrap_service import BootstrapService


def test_bootstrap_status_zero_users(client):
    resp = client.get("/api/v1/setup/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["available"] is True


def test_bootstrap_status_after_user_exists(client, create_user):
    create_user(username="existing_operator", role=UserRole.OPERATOR)

    resp = client.get("/api/v1/setup/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["available"] is False


def test_valid_bootstrap_flow(client, web_session):
    payload = {
        "username": "superadmin",
        "email": "superadmin@integra-ist.com",
        "password": "SecurePassword123!#"
    }
    resp = client.post("/api/v1/setup/bootstrap", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["username"] == "superadmin"
    assert data["data"]["role"] == "OWNER"

    # Verify user exists in database with OWNER role
    owner = web_session.query(User).filter(User.username == "superadmin").first()
    assert owner is not None
    assert owner.role == UserRole.OWNER.value
    assert owner.email == "superadmin@integra-ist.com"
    # Verify password is not plaintext
    assert owner.password_hash != "SecurePassword123!#"
    assert owner.password_hash.startswith("$2b$")

    # Verify audit trail
    audit = web_session.query(AuditLog).filter(
        AuditLog.event_type == "SYSTEM_BOOTSTRAP_OWNER_CREATED"
    ).first()
    assert audit is not None
    assert audit.actor == "superadmin"
    assert audit.payload_json["role"] == "OWNER"


def test_bootstrap_rejected_with_weak_password(client):
    payload = {
        "username": "owner",
        "email": "owner@example.com",
        "password": "weak"
    }
    resp = client.post("/api/v1/setup/bootstrap", json=payload)
    assert resp.status_code == 422
    data = resp.json()
    assert data["success"] is False
    assert "at least 12" in data["error"]["message"].lower()


def test_duplicate_bootstrap_fails_with_conflict(client):
    payload = {
        "username": "first_owner",
        "email": "first@integra-ist.com",
        "password": "CompliantPass123!#"
    }
    resp1 = client.post("/api/v1/setup/bootstrap", json=payload)
    assert resp1.status_code == 200

    # Second bootstrap attempt must be rejected with 409 Conflict
    payload2 = {
        "username": "second_owner",
        "email": "second@integra-ist.com",
        "password": "CompliantPass123!#"
    }
    resp2 = client.post("/api/v1/setup/bootstrap", json=payload2)
    assert resp2.status_code == 409
    data = resp2.json()
    assert data["success"] is False
    assert "already" in data["error"]["message"].lower()


def test_concurrent_bootstrap_sqlite_safety():
    """
    Simulates multiple concurrent threads attempting first-run bootstrap simultaneously.
    Guarantees that exactly one OWNER is created and other concurrent attempts fail safely.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    temp_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 15}
    )
    Base.metadata.create_all(temp_engine)
    SessionMaker = sessionmaker(autocommit=False, autoflush=False, bind=temp_engine)

    try:
        def attempt_bootstrap(worker_id: int):
            session = SessionMaker()
            try:
                success, message, data = BootstrapService.bootstrap_owner(
                    db=session,
                    username=f"owner_{worker_id}",
                    email=f"owner_{worker_id}@example.com",
                    password="ConcurrentPassword123!#",
                    ip_address=f"192.168.1.{worker_id}"
                )
                return success, message
            finally:
                session.close()

        thread_count = 5
        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = [executor.submit(attempt_bootstrap, i) for i in range(thread_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        success_count = sum(1 for ok, _ in results if ok)
        fail_count = sum(1 for ok, _ in results if not ok)

        # Invariant: EXACTLY ONE bootstrap request must succeed
        assert success_count == 1
        assert fail_count == thread_count - 1

        # Invariant: EXACTLY ONE user must exist in the database
        verify_session = SessionMaker()
        users = verify_session.query(User).all()
        assert len(users) == 1
        assert users[0].role == UserRole.OWNER.value
        verify_session.close()
    finally:
        temp_engine.dispose()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
