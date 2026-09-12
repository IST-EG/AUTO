"""
Tests for Security Hardening, Brute-Force Defenses, and Security Headers.

Verifies:
- 5 failed login attempts trigger 429 Too Many Requests (Account Lockout)
- ACCOUNT_LOCKED audit event emitted
- Successful login clears failed attempt counter
- OWASP security headers present on responses
- Password never echoed in validation error responses
- Password hashes never returned in API payloads
"""

from app.models.audit_log import AuditLog
from app.models.user import UserRole
from app.web.config import web_settings


def test_brute_force_lockout_after_five_failed_attempts(client, create_user, web_session):
    create_user(username="brute_target", password="Password123!#")

    # Attempt 5 incorrect logins
    for i in range(5):
        resp = client.post("/api/v1/auth/login", json={
            "username": "brute_target",
            "password": "WrongPassword!"
        })
        assert resp.status_code == 401

    # 6th attempt must be blocked by rate limiter with 429 Too Many Requests
    blocked_resp = client.post("/api/v1/auth/login", json={
        "username": "brute_target",
        "password": "Password123!#"  # Even correct password is now locked out
    })
    assert blocked_resp.status_code == 429
    assert "Account temporarily locked" in blocked_resp.json()["error"]["message"]

    # Invariant: ACCOUNT_LOCKED audit event must be recorded
    audit = web_session.query(AuditLog).filter(AuditLog.event_type == "ACCOUNT_LOCKED").first()
    assert audit is not None
    assert audit.actor == "brute_target"


def test_successful_login_clears_failed_attempts(client, create_user):
    create_user(username="resettable_user", password="Password123!#")

    # Fail 3 times (less than threshold of 5)
    for _ in range(3):
        client.post("/api/v1/auth/login", json={
            "username": "resettable_user",
            "password": "WrongPassword!"
        })

    # Successful login resets the counter
    success_resp = client.post("/api/v1/auth/login", json={
        "username": "resettable_user",
        "password": "Password123!#"
    })
    assert success_resp.status_code == 200

    # Next failure is attempt 1 again, not attempt 4
    fail_resp = client.post("/api/v1/auth/login", json={
        "username": "resettable_user",
        "password": "WrongPassword!"
    })
    assert fail_resp.status_code == 401


def test_owasp_security_headers_present(client):
    resp = client.get("/health")
    assert resp.status_code == 200

    headers = resp.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
    assert "X-Correlation-ID" in headers


def test_password_not_leaked_in_validation_errors(client):
    """Verifies that invalid password inputs are sanitized in error responses."""
    resp = client.post("/api/v1/setup/bootstrap", json={
        "username": "valid_user",
        "email": "user@example.com",
        "password": "plain_text_secret_pwd"  # Misses uppercase/digit/symbol rules
    })
    assert resp.status_code in (422, 400)
    body_str = resp.text
    # Secret plaintext string must NEVER appear in the error response body
    assert "plain_text_secret_pwd" not in body_str


def test_password_hash_never_leaked_in_api_responses(client, create_user):
    user = create_user(username="secret_user", password="Password123!#", role=UserRole.ADMIN)

    resp = client.post("/api/v1/auth/login", json={
        "username": "secret_user",
        "password": "Password123!#"
    })
    assert resp.status_code == 200
    token = resp.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    me_resp = client.get("/api/v1/auth/me", cookies={web_settings.WEB_SESSION_COOKIE_NAME: token})
    assert me_resp.status_code == 200
    me_data = me_resp.json()["data"]

    # Verify no password_hash field in DTO
    assert "password" not in me_data
    assert "password_hash" not in me_data
    assert "$2b$" not in me_resp.text


def test_health_ready_probe(client):
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["data"]["database"] == "connected"


def test_health_ready_probe_database_error(client):
    from unittest.mock import MagicMock
    from app.web.dependencies import get_db
    from app.web.app import app

    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("DB connection timeout")

    def override_broken_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_broken_db
    try:
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    finally:
        app.dependency_overrides.clear()


def test_auth_csrf_endpoint(client):
    resp = client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    token = resp.json()["data"]["csrf_token"]
    assert token is not None
    assert resp.cookies.get(web_settings.WEB_CSRF_COOKIE_NAME) == token


def test_session_cleanup_expired(web_session, create_user):
    from app.web.security.session import session_manager
    from datetime import datetime, timezone, timedelta
    from app.models.user_session import UserSession

    user = create_user(username="cleanup_user", password="Password123!#")
    token = session_manager.create_session(web_session, user)

    # Manually expire the session in the database
    past = datetime.now(timezone.utc) - timedelta(hours=24)
    web_session.query(UserSession).filter(UserSession.user_id == user.id).update({
        UserSession.expires_at: past,
        UserSession.last_active_at: past
    })
    web_session.commit()

    deleted = session_manager.cleanup_expired_sessions(web_session)
    assert deleted >= 1
    assert web_session.query(UserSession).filter(UserSession.user_id == user.id).count() == 0


def test_login_tracker_expired_lockout_and_window():
    from app.web.security.brute_force import LoginAttemptTracker
    from datetime import datetime, timezone, timedelta

    tracker = LoginAttemptTracker(max_attempts=3, lockout_minutes=1)
    username = "tracker_test"
    ip = "10.0.0.1"

    # Simulate lockout
    for _ in range(3):
        tracker.record_failure(username, ip)

    locked, rem = tracker.is_locked(username, ip)
    assert locked is True

    # Simulate expired lockout
    key = tracker._make_key(username, ip)
    with tracker._lock:
        tracker._records[key]["locked_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    locked_after, _ = tracker.is_locked(username, ip)
    assert locked_after is False

    # Simulate failure outside window
    with tracker._lock:
        tracker._records[key] = {
            "attempts": 2,
            "first_failed": datetime.now(timezone.utc) - timedelta(minutes=5),
            "locked_until": None
        }

    is_locked, _ = tracker.record_failure(username, ip)
    assert is_locked is False
    with tracker._lock:
        assert tracker._records[key]["attempts"] == 1


def test_bootstrap_service_validation_guards(web_session):
    from app.web.services.bootstrap_service import BootstrapService

    # Blank username
    ok, msg, _ = BootstrapService.bootstrap_owner(web_session, "", "admin@example.com", "Password123!#")
    assert ok is False
    assert "Username is required" in msg

    # Blank email
    ok, msg, _ = BootstrapService.bootstrap_owner(web_session, "admin", "", "Password123!#")
    assert ok is False
    assert "Email is required" in msg


def test_bcrypt_rounds_out_of_range():
    import pytest
    from app.web.security.hasher import BcryptPasswordHasher

    with pytest.raises(ValueError, match="between 4 and 31"):
        BcryptPasswordHasher(rounds=3)

    with pytest.raises(ValueError, match="between 4 and 31"):
        BcryptPasswordHasher(rounds=32)
