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

