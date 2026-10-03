"""
Tests for Campaign-Contact membership, Eligibility evaluation, and Draft safety controls.

Verifies:
- Evaluation of contact eligibility on campaign assignment (ELIGIBLE vs EXCLUDED)
- Skip / exclusion reasons (e.g. OPTED_OUT, INVALID_PHONE, DUPLICATE)
- Listing campaign contacts with status filters
- Contact removal permitted only in DRAFT state
- Role-based authorization and CSRF enforcement
"""

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.services.campaign_contact_service import CampaignContactService
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


def test_campaign_contact_eligibility_and_membership(web_session):
    # Setup campaign in DRAFT
    campaign = Campaign(name="Enrollment Test", message_template="Hi {{name}}")
    web_session.add(campaign)

    # Setup 3 contacts: 1 active, 1 opted out, 1 inactive
    c_active = Contact(name="Active User", phone_e164="+201011111111", country_code="20", consent_status="opted_in", contact_status="active")
    c_optout = Contact(name="OptedOut User", phone_e164="+201022222222", country_code="20", consent_status="opted_out", contact_status="active")
    c_inactive = Contact(name="Inactive User", phone_e164="+201033333333", country_code="20", consent_status="pending", contact_status="inactive")
    web_session.add_all([c_active, c_optout, c_inactive])
    web_session.commit()

    # Add contacts to campaign
    summary = CampaignContactService.add_contacts(
        web_session, campaign.id, [c_active.id, c_optout.id, c_inactive.id]
    )
    assert summary.total == 3
    assert summary.added == 1       # Active user is ELIGIBLE
    assert summary.excluded == 2    # Opted-out and inactive users are EXCLUDED

    # List contacts in campaign
    all_cc = CampaignContactService.list_campaign_contacts(web_session, campaign.id)
    assert len(all_cc) == 3

    eligible_cc = CampaignContactService.list_campaign_contacts(web_session, campaign.id, status_filter="ELIGIBLE")
    assert len(eligible_cc) == 1
    assert eligible_cc[0].contact_id == c_active.id

    excluded_cc = CampaignContactService.list_campaign_contacts(web_session, campaign.id, status_filter="EXCLUDED")
    assert len(excluded_cc) == 2


def test_campaign_contact_remove_in_draft_vs_running(web_session):
    campaign = Campaign(name="Removal Test", message_template="Hi {{name}}", status="DRAFT")
    web_session.add(campaign)
    c1 = Contact(name="User 1", phone_e164="+201044444444", country_code="20", consent_status="pending", contact_status="active")
    web_session.add(c1)
    web_session.commit()

    CampaignContactService.add_contacts(web_session, campaign.id, [c1.id])

    # 1. Removal succeeds in DRAFT state
    removed = CampaignContactService.remove_contact(web_session, campaign.id, c1.id)
    assert removed is True
    assert len(CampaignContactService.list_campaign_contacts(web_session, campaign.id)) == 0

    # 2. Add contact back, transition campaign to RUNNING
    CampaignContactService.add_contacts(web_session, campaign.id, [c1.id])
    CampaignService.transition_status(web_session, campaign.id, "RUNNING")

    # 3. Removal must fail in RUNNING state
    with pytest.raises(HTTPException) as exc_info:
        CampaignContactService.remove_contact(web_session, campaign.id, c1.id)
    assert exc_info.value.status_code == 400
    assert "Only DRAFT campaigns allow removals" in str(exc_info.value.detail)


def test_campaign_contact_api_endpoints(client, web_session, create_user):
    operator = create_user("enroll_op", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    # Setup campaign & contact
    campaign = Campaign(name="API CC Test", message_template="Hello {{name}}", status="DRAFT")
    web_session.add(campaign)
    contact = Contact(name="Enrolled Person", phone_e164="+201055555555", country_code="20", consent_status="pending", contact_status="active")
    web_session.add(contact)
    web_session.commit()

    # 1. Add contact via POST
    add_resp = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_ids": [contact.id]},
        cookies=cookies,
        headers=headers
    )
    assert add_resp.status_code == 200
    assert add_resp.json()["data"]["total"] == 1

    # 2. List campaign contacts via GET
    list_resp = client.get(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        cookies=cookies
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]) == 1
    assert list_resp.json()["data"][0]["contact_id"] == contact.id

    # 3. Delete contact via DELETE
    del_resp = client.delete(
        f"/api/v1/campaigns/{campaign.id}/contacts/{contact.id}",
        cookies=cookies,
        headers=headers
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["data"] is True


def test_campaign_contacts_edge_cases(web_session):
    # 1. 404 on list_campaign_contacts for non-existent campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignContactService.list_campaign_contacts(web_session, 99999)
    assert exc_info.value.status_code == 404

    # 2. 404 on add_contacts for non-existent campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignContactService.add_contacts(web_session, 99999, [1, 2])
    assert exc_info.value.status_code == 404

    # 3. 400 on add_contacts with no matching contacts
    c = Campaign(name="Empty Enrollment", message_template="Hello {{name}}")
    web_session.add(c)
    web_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        CampaignContactService.add_contacts(web_session, c.id, [99998, 99999])
    assert exc_info.value.status_code == 400

    # 4. 404 on remove_contact for non-existent campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignContactService.remove_contact(web_session, 99999, 1)
    assert exc_info.value.status_code == 404

    # 5. 404 on remove_contact when contact is not in campaign
    with pytest.raises(HTTPException) as exc_info:
        CampaignContactService.remove_contact(web_session, c.id, 99999)
    assert exc_info.value.status_code == 404


def test_campaign_contact_enrollment_payload_formats_and_draft_invariants(client, web_session, create_user):
    """
    Verifies:
    - Enrollment succeeds using singular 'contact_id' payload format.
    - Enrollment succeeds using plural 'contact_ids' payload format.
    - Campaign status remains strictly DRAFT.
    - No queue Message records are created.
    """
    operator = create_user("op_format_test", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    campaign = Campaign(name="Draft Invariant Campaign", message_template="Hi {{name}}", status="DRAFT")
    c1 = Contact(name="Format Contact 1", phone_e164="+201099990001", country_code="20", consent_status="pending", contact_status="active")
    c2 = Contact(name="Format Contact 2", phone_e164="+201099990002", country_code="20", consent_status="pending", contact_status="active")
    web_session.add_all([campaign, c1, c2])
    web_session.commit()

    # 1. Singular payload format: {"contact_id": c1.id}
    res_singular = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id},
        cookies=cookies,
        headers=headers
    )
    assert res_singular.status_code == 200, res_singular.text
    data_sing = res_singular.json()["data"]
    assert data_sing["total"] == 1
    assert data_sing["added"] == 1

    # 2. Plural payload format: {"contact_ids": [c2.id]}
    res_plural = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_ids": [c2.id]},
        cookies=cookies,
        headers=headers
    )
    assert res_plural.status_code == 200, res_plural.text
    data_plur = res_plural.json()["data"]
    assert data_plur["total"] == 1
    assert data_plur["added"] == 1

    # Verify campaign remains DRAFT
    web_session.expire_all()
    camp_db = web_session.query(Campaign).filter(Campaign.id == campaign.id).first()
    assert camp_db.status == "DRAFT"

    # Verify no queue messages created
    msg_count = web_session.query(Message).count()
    assert msg_count == 0


def test_campaign_contact_enrollment_validation_errors(client, web_session, create_user):
    """
    Verifies that invalid requests are rejected with status 422 and structured validation error details:
    - Empty payload {}
    - Empty list {"contact_ids": []}
    - Invalid type {"contact_id": "not_an_int"}
    - Non-positive integer {"contact_id": 0}
    - Non-positive integer in list {"contact_ids": [-1]}
    """
    operator = create_user("op_val_test", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    campaign = Campaign(name="Val Test Campaign", message_template="Hi {{name}}", status="DRAFT")
    web_session.add(campaign)
    web_session.commit()

    # 1. Empty body
    r_empty = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={},
        cookies=cookies,
        headers=headers
    )
    assert r_empty.status_code == 422
    body_empty = r_empty.json()
    assert body_empty["error"]["code"] == "VALIDATION_ERROR"
    assert "At least one contact ID must be provided" in str(body_empty["error"]["details"])

    # 2. Empty list
    r_empty_list = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_ids": []},
        cookies=cookies,
        headers=headers
    )
    assert r_empty_list.status_code == 422
    body_el = r_empty_list.json()
    assert body_el["error"]["code"] == "VALIDATION_ERROR"
    assert "At least one contact ID must be provided" in str(body_el["error"]["details"])

    # 3. Invalid type
    r_invalid_type = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": "invalid_number"},
        cookies=cookies,
        headers=headers
    )
    assert r_invalid_type.status_code == 422
    body_it = r_invalid_type.json()
    assert body_it["error"]["code"] == "VALIDATION_ERROR"

    # 4. Zero ID
    r_zero = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": 0},
        cookies=cookies,
        headers=headers
    )
    assert r_zero.status_code == 422
    body_zero = r_zero.json()
    assert body_zero["error"]["code"] == "VALIDATION_ERROR"

    # 5. Negative ID
    r_neg = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_ids": [-5]},
        cookies=cookies,
        headers=headers
    )
    assert r_neg.status_code == 422
    body_neg = r_neg.json()
    assert body_neg["error"]["code"] == "VALIDATION_ERROR"
    assert "Contact IDs must be positive integers" in str(body_neg["error"]["details"])


def test_campaign_contact_enrollment_duplicates_and_nonexistent(client, web_session, create_user):
    """
    Verifies:
    - Non-existent campaign returns 404.
    - Non-existent contact returns 400.
    - Duplicate enrollment is handled safely without crashing.
    """
    operator = create_user("op_dup_test", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    campaign = Campaign(name="Dup Test Campaign", message_template="Hi {{name}}", status="DRAFT")
    c1 = Contact(name="Dup Contact", phone_e164="+201099990003", country_code="20", consent_status="pending", contact_status="active")
    web_session.add_all([campaign, c1])
    web_session.commit()

    # 1. Non-existent campaign
    r_nocamp = client.post(
        "/api/v1/campaigns/99999/contacts",
        json={"contact_id": c1.id},
        cookies=cookies,
        headers=headers
    )
    assert r_nocamp.status_code == 404

    # 2. Non-existent contact
    r_nocontact = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": 88888},
        cookies=cookies,
        headers=headers
    )
    assert r_nocontact.status_code == 400

    # 3. First enrollment succeeds
    r_first = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id},
        cookies=cookies,
        headers=headers
    )
    assert r_first.status_code == 200
    assert r_first.json()["data"]["added"] == 1
    assert r_first.json()["data"]["duplicates"] == 0

    # 4. Second enrollment handles duplicate safely
    r_second = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id},
        cookies=cookies,
        headers=headers
    )
    assert r_second.status_code == 200
    assert r_second.json()["data"]["added"] == 0
    assert r_second.json()["data"]["duplicates"] == 1


def test_campaign_contact_enrollment_security_and_csrf(client, web_session, create_user):
    """
    Verifies:
    - Unauthenticated request is rejected (401).
    - Insufficient role (e.g. VIEWER) is rejected (403).
    - Missing CSRF header is rejected (403).
    - Invalid CSRF token is rejected (403).
    """
    operator = create_user("op_sec_test", UserRole.OPERATOR)
    viewer = create_user("viewer_sec_test", UserRole.VIEWER)
    op_cookies, op_headers = _auth(web_session, operator)
    viewer_cookies, viewer_headers = _auth(web_session, viewer)

    campaign = Campaign(name="Sec Test Campaign", message_template="Hi {{name}}", status="DRAFT")
    c1 = Contact(name="Sec Contact", phone_e164="+201099990004", country_code="20", consent_status="pending", contact_status="active")
    web_session.add_all([campaign, c1])
    web_session.commit()

    # 1. Unauthenticated (no cookies)
    r_noauth = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id}
    )
    assert r_noauth.status_code == 401

    # 2. VIEWER role
    r_viewer = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id},
        cookies=viewer_cookies,
        headers=viewer_headers
    )
    assert r_viewer.status_code == 403

    # 3. Missing CSRF header
    r_nocsrf = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id},
        cookies=op_cookies
    )
    assert r_nocsrf.status_code == 403

    # 4. Invalid CSRF header
    bad_headers = {"X-CSRF-Token": "invalid_token_12345"}
    r_badcsrf = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c1.id},
        cookies=op_cookies,
        headers=bad_headers
    )
    assert r_badcsrf.status_code == 403


def test_contact_edit_phone_and_retry_endpoint(client, web_session, create_user):
    operator = create_user("op_edit_test", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    campaign = Campaign(name="Edit & Retry Campaign", message_template="Hello {{name}}", status="PAUSED")
    c = Contact(name="Original Name", phone_e164="+201088880001", country_code="20", consent_status="opted_in", contact_status="active")
    web_session.add_all([campaign, c])
    web_session.commit()

    # 1. Enroll contact
    r_add = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts",
        json={"contact_id": c.id},
        cookies=cookies,
        headers=headers
    )
    assert r_add.status_code == 200

    # 2. Edit contact phone number via PATCH /api/v1/contacts/{id}
    r_edit = client.patch(
        f"/api/v1/contacts/{c.id}",
        json={"name": "Updated Name", "phone_number": "+201088880002", "company": "New Corp"},
        cookies=cookies,
        headers=headers
    )
    assert r_edit.status_code == 200
    updated = r_edit.json()["data"]
    assert updated["name"] == "Updated Name"
    assert updated["phone_e164"] == "+201088880002"
    assert updated["company"] == "New Corp"

    # 3. Simulate failure state on campaign contact
    from app.models.campaign_contact import CampaignContact
    cc = web_session.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id == c.id).first()
    cc.status = "FAILED"
    web_session.commit()

    # 4. Retry contact via POST /api/v1/campaigns/{id}/contacts/{contact_id}/retry
    r_retry = client.post(
        f"/api/v1/campaigns/{campaign.id}/contacts/{c.id}/retry",
        cookies=cookies,
        headers=headers
    )
    assert r_retry.status_code == 200
    assert r_retry.json()["data"] is True

    web_session.refresh(cc)
    assert cc.status == "ELIGIBLE"



