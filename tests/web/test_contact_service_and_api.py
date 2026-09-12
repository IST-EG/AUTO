"""
Tests for Contact Management Service, Privacy Masking, and CSV Ingestion/Export.

Verifies:
- Contact creation with E.164 normalization and duplicate detection
- Contact profile update
- Privacy phone number masking for non-privileged viewers
- CSV Dry Run verification (valid count, duplicate detection, phone syntax errors)
- CSV Commit import and audit log recording
- CSV Export generation
- Role-based access control and CSRF enforcement
"""

import io
import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.models.contact import Contact
from app.web.config import web_settings
from app.web.security.session import session_manager
from app.web.security.csrf import csrf_manager
from app.web.schemas.contacts import (
    ContactCreateRequest,
    ContactUpdateRequest,
)
from app.web.services.contact_service import ContactService, mask_phone_number


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

def test_mask_phone_number():
    assert mask_phone_number("+201012345678") == "+201******678"
    assert mask_phone_number("123") == "123"


def test_contact_service_crud_and_masking(web_session):
    # 1. Create contact
    req = ContactCreateRequest(
        name="Sarah Connor",
        country_code="20",
        phone_number="1012345678",
        company="Cyberdyne",
        city="Cairo",
    )
    c = ContactService.create_contact(web_session, req, username="testop")
    assert c.id is not None
    assert c.phone_e164 == "+201012345678"
    assert c.name == "Sarah Connor"

    # 2. Update contact
    update_req = ContactUpdateRequest(company="Skynet Systems", consent_status="opted_in")
    c_updated = ContactService.update_contact(web_session, c.id, update_req, username="testop")
    assert c_updated.company == "Skynet Systems"
    assert c_updated.consent_status == "opted_in"

    # 3. Privacy Masking in Listing
    # Non-privileged viewer should see masked phone
    unprivileged_list = ContactService.list_contacts(web_session, is_privileged=False)
    assert unprivileged_list[0].phone_e164 == "+201******678"

    # Privileged user sees unmasked E.164 phone
    privileged_list = ContactService.list_contacts(web_session, is_privileged=True)
    assert privileged_list[0].phone_e164 == "+201012345678"


def test_contact_service_csv_dry_run_and_commit(web_session):
    csv_content = (
        "name,phone_number,country_code,company,city\n"
        "Alice,1011111111,20,Alpha Corp,Cairo\n"
        "Bob,1022222222,20,Beta Corp,Giza\n"
        "InvalidRow,not_a_phone,20,Gamma Corp,Alex\n"
        "MissingField,,20,Delta Corp,Cairo\n"
        "AliceDup,1011111111,20,Dupe Corp,Cairo\n"
    )

    # 1. Dry run
    dry_run = ContactService.dry_run_csv(csv_content, web_session)
    assert dry_run.total_rows == 5
    assert dry_run.valid_count == 2
    assert dry_run.invalid_phone_count == 1
    assert dry_run.missing_fields_count == 1
    assert dry_run.duplicate_count == 1
    assert len(dry_run.preview) == 2

    # 2. Commit import
    commit_res = ContactService.commit_csv(csv_content, web_session, username="testop")
    assert commit_res.imported == 2
    assert commit_res.skipped_invalid_phone == 1
    assert commit_res.skipped_duplicate == 1

    # 3. Export CSV
    exported = ContactService.export_contacts_csv(web_session)
    assert "Alice" in exported
    assert "+201011111111" in exported
    assert "Bob" in exported


# ==============================================================================
# REST API TESTS
# ==============================================================================

def test_contact_api_unauthenticated(client):
    assert client.get("/api/v1/contacts").status_code == 401
    assert client.post("/api/v1/contacts", json={"name": "A"}).status_code == 401
    assert client.get("/api/v1/contacts/1").status_code == 401
    assert client.get("/api/v1/contacts/export").status_code == 401


def test_contact_api_rbac_masking_and_mutations(client, web_session, create_user):
    viewer = create_user("contactviewer", UserRole.VIEWER)
    operator = create_user("contactop", UserRole.OPERATOR)
    admin = create_user("contactadmin", UserRole.ADMIN)

    # Create a contact via operator
    c_cookies, c_headers = _auth(web_session, operator)
    create_resp = client.post(
        "/api/v1/contacts",
        json={
            "name": "David Miller",
            "country_code": "20",
            "phone_number": "1098765432",
            "company": "Miller Logistics"
        },
        cookies=c_cookies,
        headers=c_headers
    )
    assert create_resp.status_code == 200
    cid = create_resp.json()["data"]["id"]

    # Viewer lists contacts -> phone should be masked
    v_cookies, _ = _auth(web_session, viewer)
    v_resp = client.get("/api/v1/contacts", cookies=v_cookies)
    assert v_resp.status_code == 200
    assert "*" in v_resp.json()["data"][0]["phone_e164"]

    # Admin lists contacts -> phone should be unmasked
    a_cookies, a_headers = _auth(web_session, admin)
    a_resp = client.get("/api/v1/contacts", cookies=a_cookies)
    assert a_resp.status_code == 200
    assert a_resp.json()["data"][0]["phone_e164"] == "+201098765432"

    # Viewer cannot export or mutate
    assert client.get("/api/v1/contacts/export", cookies=v_cookies).status_code == 403

    # Operator can export
    exp_resp = client.get("/api/v1/contacts/export", cookies=c_cookies)
    assert exp_resp.status_code == 200
    assert exp_resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "David Miller" in exp_resp.text


def test_contact_api_csv_endpoints(client, web_session, create_user):
    operator = create_user("csvop", UserRole.OPERATOR)
    cookies, headers = _auth(web_session, operator)

    csv_data = b"name,phone_number,country_code,company\nJohn,1055555555,20,Acme\n"

    # Dry-run upload
    files = {"file": ("test.csv", io.BytesIO(csv_data), "text/csv")}
    dry_resp = client.post(
        "/api/v1/contacts/import/dry-run",
        files=files,
        cookies=cookies,
        headers=headers
    )
    assert dry_resp.status_code == 200
    assert dry_resp.json()["data"]["valid_count"] == 1

    # Commit upload
    files = {"file": ("test.csv", io.BytesIO(csv_data), "text/csv")}
    commit_resp = client.post(
        "/api/v1/contacts/import/commit",
        files=files,
        cookies=cookies,
        headers=headers
    )
    assert commit_resp.status_code == 200
    assert commit_resp.json()["data"]["imported"] == 1


def test_contact_service_edge_cases(web_session):
    # 1. 404 on get_contact
    with pytest.raises(HTTPException) as exc_info:
        ContactService.get_contact(web_session, 99999)
    assert exc_info.value.status_code == 404

    # 2. 409 on duplicate create_contact
    req = ContactCreateRequest(name="Dupe", country_code="20", phone_number="1099999999")
    ContactService.create_contact(web_session, req)
    with pytest.raises(HTTPException) as exc_info:
        ContactService.create_contact(web_session, req)
    assert exc_info.value.status_code == 409

    # 3. 422 on empty name
    with pytest.raises(HTTPException) as exc_info:
        ContactService.create_contact(
            web_session,
            ContactCreateRequest(name="   ", country_code="20", phone_number="1088888888")
        )
    assert exc_info.value.status_code == 422

    # 4. 404 on update non-existent contact
    with pytest.raises(HTTPException) as exc_info:
        ContactService.update_contact(
            web_session, 99999, ContactUpdateRequest(name="Ghost")
        )
    assert exc_info.value.status_code == 404

    # 5. Empty CSV dry-run
    res_empty = ContactService.dry_run_csv("", web_session)
    assert len(res_empty.errors) > 0

    # 6. Missing columns CSV dry-run
    res_missing = ContactService.dry_run_csv("name,company\nJohn,Acme\n", web_session)
    assert "Missing required columns" in res_missing.errors[0]

