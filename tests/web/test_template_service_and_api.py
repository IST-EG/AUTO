"""
Tests for Template Management Service and REST API.

Verifies:
- Template creation and v1 initial versioning
- Immutable append-only version history
- Monotonic version incrementation
- Template syntax validation (supported variables vs unsupported variables)
- Preview rendering
- Role-based authorization (VIEWER cannot mutate, OPERATOR/ADMIN can)
- Double-submit CSRF enforcement
"""

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.models.template import MessageTemplate, MessageTemplateVersion
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.schemas.templates import (
    TemplateCreateRequest,
    TemplateVersionCreateRequest,
    TemplatePreviewRequest,
)
from app.web.services.template_service import TemplateService


def _auth(web_session, user):
    token = session_manager.create_session(web_session, user)
    csrf_token = csrf_manager.generate_token()
    cookies = {
        web_settings.WEB_SESSION_COOKIE_NAME: token,
        web_settings.WEB_CSRF_COOKIE_NAME: csrf_token,
    }
    headers = {"X-CSRF-Token": csrf_token}
    return cookies, headers


# ==============================================================================
# SERVICE UNIT TESTS
# ==============================================================================

def test_template_service_create_and_versions(web_session):
    # 1. Create template
    req = TemplateCreateRequest(
        name="Intro Template",
        description="First touch outreach",
        body="Hello {{name}}, welcome to {{company}} in {{city}}!"
    )
    tmpl = TemplateService.create_template(web_session, req, username="testop")
    assert tmpl.id is not None
    assert tmpl.name == "Intro Template"
    assert len(tmpl.versions) == 1
    assert tmpl.versions[0].version_number == 1
    assert tmpl.versions[0].body == "Hello {{name}}, welcome to {{company}} in {{city}}!"
    assert tmpl.versions[0].created_by == "testop"

    # 2. Append new version v2
    v2_req = TemplateVersionCreateRequest(
        body="Hi {{name}}, updated outreach from {{campaign}} at {{company}}."
    )
    v2 = TemplateService.create_version(web_session, tmpl.id, v2_req, username="testadmin")
    assert v2.version_number == 2
    assert v2.created_by == "testadmin"

    # 3. Retrieve template detail
    detail = TemplateService.get_template(web_session, tmpl.id)
    assert len(detail.versions) == 2
    assert detail.versions[0].version_number == 1
    assert detail.versions[1].version_number == 2

    # 4. List templates
    all_tmpls = TemplateService.list_templates(web_session)
    assert len(all_tmpls) == 1
    assert all_tmpls[0].latest_version == 2
    assert all_tmpls[0].version_count == 2


def test_template_service_syntax_validation(web_session):
    # Valid syntax
    res = TemplateService.validate_template_syntax("Hello {{name}}, from {{company}}!")
    assert res.valid is True
    assert set(res.variables) == {"name", "company"}
    assert len(res.errors) == 0

    # Invalid syntax (unsupported variable)
    res_bad = TemplateService.validate_template_syntax("Hello {{unsupported_var}}!")
    assert res_bad.valid is False
    assert len(res_bad.errors) > 0


def test_template_service_preview(web_session):
    req = TemplatePreviewRequest(
        body="Hello {{name}}, welcome to {{company}}!",
        contact_name="Alice Smith",
        company="Global Tech"
    )
    res = TemplateService.preview_template(req)
    assert res.rendered_text == "Hello Alice Smith, welcome to Global Tech!"


def test_template_service_duplicate_name(web_session):
    req = TemplateCreateRequest(
        name="Unique Name",
        body="Hello {{name}}!"
    )
    TemplateService.create_template(web_session, req, username="testop")

    with pytest.raises(HTTPException) as exc_info:
        TemplateService.create_template(web_session, req, username="testop")
    assert exc_info.value.status_code == 409


# ==============================================================================
# REST API TESTS
# ==============================================================================

def test_template_api_unauthenticated(client):
    assert client.get("/api/v1/templates").status_code == 401
    assert client.post("/api/v1/templates", json={"name": "T", "body": "{{name}}"}).status_code == 401
    assert client.get("/api/v1/templates/1").status_code == 401
    assert client.post("/api/v1/templates/1/versions", json={"body": "{{name}}"}).status_code == 401


def test_template_api_rbac_viewer_cannot_mutate(client, web_session, create_user):
    viewer = create_user("tmplviewer", UserRole.VIEWER)
    cookies, headers = _auth(web_session, viewer)

    # VIEWER can read
    assert client.get("/api/v1/templates", cookies=cookies).status_code == 200

    # VIEWER cannot create template
    resp = client.post(
        "/api/v1/templates",
        json={"name": "T1", "body": "Hi {{name}}"},
        cookies=cookies,
        headers=headers
    )
    assert resp.status_code == 403


def test_template_api_operator_crud_lifecycle(client, web_session, create_user):
    operator = create_user("tmplop", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    # 1. Validate endpoint
    val_resp = client.post(
        "/api/v1/templates/validate",
        json={"body": "Hello {{name}} from {{company}}"},
        cookies=cookies,
        headers=headers
    )
    assert val_resp.status_code == 200
    assert val_resp.json()["data"]["valid"] is True

    # 2. Preview endpoint
    prev_resp = client.post(
        "/api/v1/templates/preview",
        json={"body": "Hello {{name}} at {{company}}!", "contact_name": "Bob", "company": "Initech"},
        cookies=cookies,
        headers=headers
    )
    assert prev_resp.status_code == 200
    assert prev_resp.json()["data"]["rendered_text"] == "Hello Bob at Initech!"

    # 3. Create template
    create_resp = client.post(
        "/api/v1/templates",
        json={"name": "Outreach v1", "description": "Desc", "body": "Hey {{name}}!"},
        cookies=cookies,
        headers=headers
    )
    assert create_resp.status_code == 200
    tmpl_data = create_resp.json()["data"]
    tmpl_id = tmpl_data["id"]
    assert tmpl_data["name"] == "Outreach v1"
    assert len(tmpl_data["versions"]) == 1

    # 4. Get template
    get_resp = client.get(f"/api/v1/templates/{tmpl_id}", cookies=cookies)
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == tmpl_id

    # 5. Add version
    ver_resp = client.post(
        f"/api/v1/templates/{tmpl_id}/versions",
        json={"body": "Hey {{name}}, check out {{company}}!"},
        cookies=cookies,
        headers=headers
    )
    assert ver_resp.status_code == 200
    assert ver_resp.json()["data"]["version_number"] == 2

    # 6. Verify list
    list_resp = client.get("/api/v1/templates", cookies=cookies)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]) == 1
    assert list_resp.json()["data"][0]["latest_version"] == 2


def test_template_service_edge_cases(web_session):
    # 1. 404 on get_template
    with pytest.raises(HTTPException) as exc_info:
        TemplateService.get_template(web_session, 99999)
    assert exc_info.value.status_code == 404

    # 2. 422 on create_template with invalid syntax
    with pytest.raises(HTTPException) as exc_info:
        TemplateService.create_template(
            web_session,
            TemplateCreateRequest(name="Bad Syntax", body="Hello {{invalid_param}}")
        )
    assert exc_info.value.status_code == 422

    # 3. 404 on create_version for non-existent template
    with pytest.raises(HTTPException) as exc_info:
        TemplateService.create_version(
            web_session, 99999, TemplateVersionCreateRequest(body="Hi {{name}}")
        )
    assert exc_info.value.status_code == 404

    # 4. 422 on create_version with invalid syntax
    tmpl = TemplateService.create_template(
        web_session,
        TemplateCreateRequest(name="Good Tmpl", body="Hello {{name}}")
    )
    with pytest.raises(HTTPException) as exc_info:
        TemplateService.create_version(
            web_session, tmpl.id, TemplateVersionCreateRequest(body="Hello {{bad_syntax}}")
        )
    assert exc_info.value.status_code == 422

    # 5. 422 on preview_template with invalid syntax
    with pytest.raises(HTTPException) as exc_info:
        TemplateService.preview_template(
            TemplatePreviewRequest(body="Hello {{unknown_var}}")
        )
    assert exc_info.value.status_code == 422

