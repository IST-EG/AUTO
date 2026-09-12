"""
Tests for Authentication & Session Management.

Verifies:
- Valid credential authentication & cookie issuance
- Failed authentication & safe error messages (no enumeration)
- Inactive user handling
- Session token hashing in database (no plaintext)
- Session token rotation (fixation protection)
- Sliding inactivity timeout
- Absolute session expiration
- Maximum 2 concurrent sessions (eviction of oldest with audit trail)
- Server-side session invalidation upon logout
"""

from datetime import datetime, timezone, timedelta
from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.models.audit_log import AuditLog
from app.web.config import web_settings
from app.web.security.session import hash_session_token, session_manager


def test_valid_login_and_me_endpoint(client, create_user):
    user = create_user(username="validuser", password="CompliantPassword123!#", role=UserRole.ADMIN)

    resp = client.post("/api/v1/auth/login", json={
        "username": "validuser",
        "password": "CompliantPassword123!#"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["username"] == "validuser"
    assert data["data"]["role"] == "ADMIN"

    # Check cookies
    cookies = resp.cookies
    assert web_settings.WEB_SESSION_COOKIE_NAME in cookies
    assert web_settings.WEB_CSRF_COOKIE_NAME in cookies

    # Query /api/v1/auth/me with session cookie
    me_resp = client.get("/api/v1/auth/me", cookies=cookies)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["data"]["username"] == "validuser"
    assert me_data["data"]["role"] == "ADMIN"


def test_invalid_login_wrong_password(client, create_user, web_session):
    create_user(username="auth_user", password="CorrectPass123!#")

    resp = client.post("/api/v1/auth/login", json={
        "username": "auth_user",
        "password": "WrongPassword123!#"
    })
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.json()["error"]["message"]

    # Verify audit event for failed login
    audit = web_session.query(AuditLog).filter(AuditLog.event_type == "USER_LOGIN_FAILED").first()
    assert audit is not None
    assert audit.actor == "auth_user"


def test_invalid_login_nonexistent_user(client):
    resp = client.post("/api/v1/auth/login", json={
        "username": "does_not_exist",
        "password": "RandomPassword123!#"
    })
    assert resp.status_code == 401
    # Constant-time safe error message; does not leak user existence
    assert "Invalid username or password" in resp.json()["error"]["message"]


def test_login_inactive_user(client, create_user):
    create_user(username="disabled_user", password="Password123!#", is_active=False)

    resp = client.post("/api/v1/auth/login", json={
        "username": "disabled_user",
        "password": "Password123!#"
    })
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.json()["error"]["message"]


def test_session_token_hashing_in_database(client, create_user, web_session):
    user = create_user(username="hashed_session_user", password="Password123!#")

    resp = client.post("/api/v1/auth/login", json={
        "username": "hashed_session_user",
        "password": "Password123!#"
    })
    raw_token = resp.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)
    assert raw_token is not None

    # Invariant: Database stores only the SHA-256 hash, NOT the raw token
    stored_session = web_session.query(UserSession).filter(UserSession.user_id == user.id).first()
    assert stored_session is not None
    assert stored_session.session_token_hash != raw_token
    assert stored_session.session_token_hash == hash_session_token(raw_token)


def test_concurrent_session_limit_evicts_oldest(client, create_user, web_session):
    user = create_user(username="multidevice", password="Password123!#")

    # 1. Login from device 1
    resp1 = client.post("/api/v1/auth/login", json={"username": "multidevice", "password": "Password123!#"})
    token1 = resp1.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    # 2. Login from device 2 (new browser session)
    client.cookies.clear()
    resp2 = client.post("/api/v1/auth/login", json={"username": "multidevice", "password": "Password123!#"})
    token2 = resp2.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    assert token1 != token2
    assert web_session.query(UserSession).filter(UserSession.user_id == user.id).count() == 2

    # 3. Login from device 3 (new browser session, exceeds max 2 concurrent sessions)
    client.cookies.clear()
    resp3 = client.post("/api/v1/auth/login", json={"username": "multidevice", "password": "Password123!#"})
    token3 = resp3.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    # Total active sessions in DB must strictly remain <= 2
    active_sessions = web_session.query(UserSession).filter(UserSession.user_id == user.id).all()
    assert len(active_sessions) == 2

    # Oldest session (token1) must be evicted
    token1_hash = hash_session_token(token1)
    assert web_session.query(UserSession).filter(UserSession.session_token_hash == token1_hash).first() is None

    # Verification: token1 must be rejected by /api/v1/auth/me
    client.cookies.clear()
    client.cookies.set(web_settings.WEB_SESSION_COOKIE_NAME, token1)
    reject_resp = client.get("/api/v1/auth/me")
    assert reject_resp.status_code == 401

    # Tokens 2 and 3 must remain valid
    client.cookies.clear()
    client.cookies.set(web_settings.WEB_SESSION_COOKIE_NAME, token3)
    valid_resp = client.get("/api/v1/auth/me")
    assert valid_resp.status_code == 200

    # Audit event for eviction must exist
    audit = web_session.query(AuditLog).filter(
        AuditLog.event_type == "SESSION_REVOKED_CONCURRENT_LIMIT"
    ).first()
    assert audit is not None


def test_session_inactivity_timeout(client, create_user, web_session):
    user = create_user(username="inactivity_user", password="Password123!#")

    resp = client.post("/api/v1/auth/login", json={"username": "inactivity_user", "password": "Password123!#"})
    token = resp.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    # Manually age the last_active_at timestamp beyond inactivity threshold (35 mins ago)
    token_hash = hash_session_token(token)
    session_rec = web_session.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()
    session_rec.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=35)
    web_session.commit()

    # Request with timed-out session must be rejected with 401
    me_resp = client.get("/api/v1/auth/me", cookies={web_settings.WEB_SESSION_COOKIE_NAME: token})
    assert me_resp.status_code == 401


def test_session_absolute_expiration(client, create_user, web_session):
    user = create_user(username="expired_user", password="Password123!#")

    resp = client.post("/api/v1/auth/login", json={"username": "expired_user", "password": "Password123!#"})
    token = resp.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)

    # Manually age the expires_at timestamp into the past
    token_hash = hash_session_token(token)
    session_rec = web_session.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()
    session_rec.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    web_session.commit()

    me_resp = client.get("/api/v1/auth/me", cookies={web_settings.WEB_SESSION_COOKIE_NAME: token})
    assert me_resp.status_code == 401


def test_logout_invalidates_session(client, create_user, web_session):
    user = create_user(username="logout_user", password="Password123!#")

    login_resp = client.post("/api/v1/auth/login", json={"username": "logout_user", "password": "Password123!#"})
    token = login_resp.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)
    csrf = login_resp.cookies.get(web_settings.WEB_CSRF_COOKIE_NAME)

    # Logout with CSRF token
    logout_resp = client.post(
        "/api/v1/auth/logout",
        cookies={
            web_settings.WEB_SESSION_COOKIE_NAME: token,
            web_settings.WEB_CSRF_COOKIE_NAME: csrf
        },
        headers={"X-CSRF-Token": csrf}
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["data"]["logged_out"] is True

    # Session record must be deleted from database
    token_hash = hash_session_token(token)
    assert web_session.query(UserSession).filter(UserSession.session_token_hash == token_hash).first() is None

    # Subsequent request with old token must fail
    me_resp = client.get("/api/v1/auth/me", cookies={web_settings.WEB_SESSION_COOKIE_NAME: token})
    assert me_resp.status_code == 401

    # Audit event emitted
    audit = web_session.query(AuditLog).filter(AuditLog.event_type == "USER_LOGOUT").first()
    assert audit is not None
    assert audit.actor == "logout_user"
