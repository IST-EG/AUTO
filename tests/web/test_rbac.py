"""
Tests for Role-Based Access Control (RBAC) foundation.

Verifies:
- All four roles (OWNER, ADMIN, OPERATOR, VIEWER) against role-gated routes
- 401 Unauthorized for unauthenticated callers
- 403 Forbidden for authenticated callers with insufficient privileges
- Full role resolution and permission enforcement
"""

import pytest
from fastapi import APIRouter, Depends
from app.models.user import User, UserRole
from app.web.dependencies import (
    get_current_user,
    require_owner,
    require_admin,
    require_operator,
    require_roles
)
from app.web.config import web_settings
from app.web.security.session import session_manager

# Register temporary test routes on an isolated router to test dependency matrix
rbac_test_router = APIRouter(prefix="/api/v1/test-rbac")

@rbac_test_router.get("/owner-only", dependencies=[Depends(require_owner)])
def owner_endpoint(user: User = Depends(get_current_user)):
    return {"role": user.role, "access": "owner"}

@rbac_test_router.get("/admin-level", dependencies=[Depends(require_admin)])
def admin_endpoint(user: User = Depends(get_current_user)):
    return {"role": user.role, "access": "admin"}

@rbac_test_router.get("/operator-level", dependencies=[Depends(require_operator)])
def operator_endpoint(user: User = Depends(get_current_user)):
    return {"role": user.role, "access": "operator"}

@rbac_test_router.get("/viewer-allowed", dependencies=[Depends(require_roles(UserRole.VIEWER, UserRole.OPERATOR, UserRole.ADMIN, UserRole.OWNER))])
def viewer_endpoint(user: User = Depends(get_current_user)):
    return {"role": user.role, "access": "viewer"}


@pytest.fixture(autouse=True)
def mount_rbac_router(client):
    """Mounts the test RBAC router to the FastAPI test app."""
    from app.web.app import app
    app.include_router(rbac_test_router)
    yield


def _login_as(client, username, role, create_user, web_session):
    user = create_user(username=username, role=role, password="Password123!#")
    token = session_manager.create_session(web_session, user)
    return {web_settings.WEB_SESSION_COOKIE_NAME: token}


def test_unauthenticated_request_rejected(client):
    resp = client.get("/api/v1/test-rbac/owner-only")
    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["error"]["message"]


def test_owner_role_permissions(client, create_user, web_session):
    cookies = _login_as(client, "the_owner", UserRole.OWNER, create_user, web_session)

    # OWNER has access to all tiers
    assert client.get("/api/v1/test-rbac/owner-only", cookies=cookies).status_code == 200
    assert client.get("/api/v1/test-rbac/admin-level", cookies=cookies).status_code == 200
    assert client.get("/api/v1/test-rbac/operator-level", cookies=cookies).status_code == 200
    assert client.get("/api/v1/test-rbac/viewer-allowed", cookies=cookies).status_code == 200


def test_admin_role_permissions(client, create_user, web_session):
    cookies = _login_as(client, "an_admin", UserRole.ADMIN, create_user, web_session)

    # ADMIN blocked from owner-only
    assert client.get("/api/v1/test-rbac/owner-only", cookies=cookies).status_code == 403
    # ADMIN allowed for admin-level and below
    assert client.get("/api/v1/test-rbac/admin-level", cookies=cookies).status_code == 200
    assert client.get("/api/v1/test-rbac/operator-level", cookies=cookies).status_code == 200
    assert client.get("/api/v1/test-rbac/viewer-allowed", cookies=cookies).status_code == 200


def test_operator_role_permissions(client, create_user, web_session):
    cookies = _login_as(client, "an_operator", UserRole.OPERATOR, create_user, web_session)

    # OPERATOR blocked from owner-only and admin-level
    assert client.get("/api/v1/test-rbac/owner-only", cookies=cookies).status_code == 403
    assert client.get("/api/v1/test-rbac/admin-level", cookies=cookies).status_code == 403
    # OPERATOR allowed for operator-level and below
    assert client.get("/api/v1/test-rbac/operator-level", cookies=cookies).status_code == 200
    assert client.get("/api/v1/test-rbac/viewer-allowed", cookies=cookies).status_code == 200


def test_viewer_role_permissions(client, create_user, web_session):
    cookies = _login_as(client, "a_viewer", UserRole.VIEWER, create_user, web_session)

    # VIEWER blocked from mutating tiers
    assert client.get("/api/v1/test-rbac/owner-only", cookies=cookies).status_code == 403
    assert client.get("/api/v1/test-rbac/admin-level", cookies=cookies).status_code == 403
    assert client.get("/api/v1/test-rbac/operator-level", cookies=cookies).status_code == 403
    # VIEWER allowed for read-only viewer tier
    assert client.get("/api/v1/test-rbac/viewer-allowed", cookies=cookies).status_code == 200
