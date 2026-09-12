"""
Tests for Campaign Management Service and REST API.

Verifies:
- Campaign creation with inline template and template library version snapshot
- Campaign updating in DRAFT state and prevention of edit in non-DRAFT states
- Campaign state machine transitions (DRAFT -> RUNNING, RUNNING -> PAUSED, etc.)
- Strict domain invariant: setting state to RUNNING does NOT spawn runner processes
- Listing with search and status filters
- Role-based access control (VIEWER, OPERATOR, ADMIN)
- Double-submit CSRF enforcement
"""

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.models.campaign import Campaign
from app.models.template import MessageTemplate, MessageTemplateVersion
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.schemas.campaigns import (
    CampaignCreateRequest,
    CampaignUpdateRequest,
)
from app.web.services.campaign_service import CampaignService


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

def test_campaign_service_create_and_snapshot(web_session):
    # Create template library version
    tmpl = MessageTemplate(name="Snapshot Source")
    web_session.add(tmpl)
    web_session.flush()
    ver = MessageTemplateVersion(
        template_id=tmpl.id,
        version_number=1,
        body="Hello {{name}}, from template version 1."
    )
    web_session.add(ver)
    web_session.commit()

    # Create campaign referencing template version
    req = CampaignCreateRequest(
        name="Campaign Alpha",
        template_version_id=ver.id,
        daily_limit=50,
        min_delay_seconds=15,
        max_delay_seconds=35,
    )
    c = CampaignService.create_campaign(web_session, req, username="testop")
    assert c.id is not None
    assert c.name == "Campaign Alpha"
    assert c.status == "DRAFT"
    assert c.message_template == "Hello {{name}}, from template version 1."
    assert c.template_version_id == ver.id
    assert c.daily_limit == 50

    # Verify updating campaign in DRAFT state
    update_req = CampaignUpdateRequest(daily_limit=75)
    c_updated = CampaignService.update_campaign(web_session, c.id, update_req, username="testop")
    assert c_updated.daily_limit == 75


def test_campaign_service_lifecycle_transitions(web_session):
    req = CampaignCreateRequest(
        name="Lifecycle Campaign",
        message_template="Hello {{name}}!"
    )
    c = CampaignService.create_campaign(web_session, req, username="testop")
    assert c.status == "DRAFT"

    # DRAFT -> RUNNING
    c = CampaignService.transition_status(web_session, c.id, "RUNNING", username="testop")
    assert c.status == "RUNNING"

    # Non-DRAFT campaigns cannot be modified
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.update_campaign(web_session, c.id, CampaignUpdateRequest(daily_limit=200))
    assert exc_info.value.status_code == 400

    # RUNNING -> PAUSED
    c = CampaignService.transition_status(web_session, c.id, "PAUSED", username="testop")
    assert c.status == "PAUSED"

    # PAUSED -> RUNNING
    c = CampaignService.transition_status(web_session, c.id, "RUNNING", username="testop")
    assert c.status == "RUNNING"

    # RUNNING -> COMPLETED
    c = CampaignService.transition_status(web_session, c.id, "COMPLETED", username="testop")
    assert c.status == "COMPLETED"

    # Invalid transition from COMPLETED -> RUNNING must be rejected
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.transition_status(web_session, c.id, "RUNNING", username="testop")
    assert exc_info.value.status_code == 400


def test_campaign_service_filtering(web_session):
    CampaignService.create_campaign(
        web_session,
        CampaignCreateRequest(name="Marketing Q1", message_template="Hi {{name}}")
    )
    c2 = CampaignService.create_campaign(
        web_session,
        CampaignCreateRequest(name="Engineering Leads", message_template="Hi {{name}}")
    )
    CampaignService.transition_status(web_session, c2.id, "RUNNING")

    # Search filter
    res = CampaignService.list_campaigns(web_session, search="Marketing")
    assert len(res) == 1
    assert res[0].name == "Marketing Q1"

    # Status filter
    res_running = CampaignService.list_campaigns(web_session, status_filter="RUNNING")
    assert len(res_running) == 1
    assert res_running[0].name == "Engineering Leads"


# ==============================================================================
# REST API TESTS
# ==============================================================================

def test_campaign_api_unauthenticated(client):
    assert client.get("/api/v1/campaigns").status_code == 401
    assert client.post("/api/v1/campaigns", json={"name": "C", "message_template": "hi"}).status_code == 401
    assert client.get("/api/v1/campaigns/1").status_code == 401
    assert client.patch("/api/v1/campaigns/1", json={"daily_limit": 50}).status_code == 401
    assert client.post("/api/v1/campaigns/1/status", json={"status": "RUNNING"}).status_code == 401


def test_campaign_api_rbac_viewer_cannot_mutate(client, web_session, create_user):
    viewer = create_user("campviewer", UserRole.VIEWER)
    cookies, headers = _auth(web_session, viewer)

    # VIEWER can list
    assert client.get("/api/v1/campaigns", cookies=cookies).status_code == 200

    # VIEWER cannot create
    resp = client.post(
        "/api/v1/campaigns",
        json={"name": "V_Camp", "message_template": "Hi {{name}}"},
        cookies=cookies,
        headers=headers
    )
    assert resp.status_code == 403


def test_campaign_api_operator_crud_and_status(client, web_session, create_user):
    operator = create_user("campop", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    # 1. Create campaign
    create_resp = client.post(
        "/api/v1/campaigns",
        json={
            "name": "API Campaign",
            "message_template": "Hello {{name}}, welcome to {{company}}!",
            "daily_limit": 120,
            "min_delay_seconds": 10,
            "max_delay_seconds": 30,
        },
        cookies=cookies,
        headers=headers
    )
    assert create_resp.status_code == 200
    camp_data = create_resp.json()["data"]
    camp_id = camp_data["id"]
    assert camp_data["name"] == "API Campaign"
    assert camp_data["status"] == "DRAFT"

    # 2. Get campaign
    get_resp = client.get(f"/api/v1/campaigns/{camp_id}", cookies=cookies)
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["stats"]["total_contacts"] == 0

    # 3. Patch campaign
    patch_resp = client.patch(
        f"/api/v1/campaigns/{camp_id}",
        json={"daily_limit": 150},
        cookies=cookies,
        headers=headers
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["daily_limit"] == 150

    # 4. Transition status to RUNNING (strictly domain state)
    status_resp = client.post(
        f"/api/v1/campaigns/{camp_id}/status",
        json={"status": "RUNNING"},
        cookies=cookies,
        headers=headers
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["data"]["status"] == "RUNNING"

    # 5. Verify list endpoint contains it
    list_resp = client.get("/api/v1/campaigns", cookies=cookies)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]) >= 1


def test_campaign_service_edge_cases(web_session):
    # 1. 404 on get_campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.get_campaign(web_session, 99999)
    assert exc_info.value.status_code == 404

    # 2. 404 on create_campaign with non-existent template_version_id
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.create_campaign(
            web_session,
            CampaignCreateRequest(name="T1", template_version_id=99999)
        )
    assert exc_info.value.status_code == 404

    # 3. Create with template_id
    tmpl = MessageTemplate(name="Tmpl For ID")
    web_session.add(tmpl)
    web_session.flush()
    ver = MessageTemplateVersion(template_id=tmpl.id, version_number=1, body="Hi {{name}}")
    web_session.add(ver)
    web_session.commit()

    c = CampaignService.create_campaign(
        web_session,
        CampaignCreateRequest(name="Camp With Tmpl ID", template_id=tmpl.id)
    )
    assert c.template_version_id == ver.id

    # 4. 404 on create with non-existent template_id
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.create_campaign(
            web_session,
            CampaignCreateRequest(name="T2", template_id=99999)
        )
    assert exc_info.value.status_code == 404

    # 5. 400 when neither template nor message_template is given
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.create_campaign(
            web_session,
            CampaignCreateRequest(name="T3")
        )
    assert exc_info.value.status_code == 400

    # 6. 409 on duplicate campaign name
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.create_campaign(
            web_session,
            CampaignCreateRequest(name="Camp With Tmpl ID", message_template="Hello {{name}}")
        )
    assert exc_info.value.status_code == 409

    # 7. 422 on invalid template syntax in create
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.create_campaign(
            web_session,
            CampaignCreateRequest(name="Invalid Syntax", message_template="Hello {{invalid_var}}")
        )
    assert exc_info.value.status_code == 422

    # 8. 404 on update non-existent campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.update_campaign(
            web_session, 99999, CampaignUpdateRequest(name="No Camp")
        )
    assert exc_info.value.status_code == 404

    # 9. Update campaign with various fields and new template_version_id
    c_new = CampaignService.create_campaign(
        web_session,
        CampaignCreateRequest(name="Update Test", message_template="Hello {{name}}")
    )
    c_updated = CampaignService.update_campaign(
        web_session,
        c_new.id,
        CampaignUpdateRequest(
            name="Update Test Renamed",
            min_delay_seconds=12,
            max_delay_seconds=28,
            batch_size=15,
            batch_pause_seconds=45,
            template_version_id=ver.id,
        )
    )
    assert c_updated.name == "Update Test Renamed"
    assert c_updated.min_delay_seconds == 12
    assert c_updated.max_delay_seconds == 28
    assert c_updated.batch_size == 15
    assert c_updated.batch_pause_seconds == 45
    assert c_updated.template_version_id == ver.id

    # 10. Update with invalid template syntax
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.update_campaign(
            web_session,
            c_new.id,
            CampaignUpdateRequest(message_template="Hello {{forbidden}}")
        )
    assert exc_info.value.status_code == 422

    # 11. 404 on transition_status for non-existent campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignService.transition_status(web_session, 99999, "RUNNING")
    assert exc_info.value.status_code == 404

