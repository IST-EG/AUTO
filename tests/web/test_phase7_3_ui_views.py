"""
Tests for Phase 7.3 UI Views (Campaigns, Templates, Contacts).

Verifies:
- Unauthenticated access redirects to /login
- Authenticated requests render HTML pages with expected context
"""

import pytest
from app.models.user import UserRole
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.template import MessageTemplate, MessageTemplateVersion
from app.web.config import web_settings
from app.web.security.session import session_manager


def _auth(web_session, user):
    token = session_manager.create_session(web_session, user)
    return {web_settings.WEB_SESSION_COOKIE_NAME: token}


def test_ui_views_unauthenticated_redirects(client, create_user):
    create_user("systemowner", UserRole.OWNER)
    routes = [
        "/campaigns",
        "/campaigns/new",
        "/campaigns/1",
        "/templates",
        "/templates/new",
        "/templates/1",
        "/contacts",
        "/contacts/import",
        "/contacts/1",
    ]
    for r in routes:
        resp = client.get(r, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


def test_ui_views_authenticated_rendering(client, web_session, create_user):
    user = create_user("ui_viewer", UserRole.OPERATOR)
    cookies = _auth(web_session, user)

    # Seed data
    tmpl = MessageTemplate(name="UI Template")
    web_session.add(tmpl)
    web_session.flush()
    ver = MessageTemplateVersion(template_id=tmpl.id, version_number=1, body="Hello {{name}}")
    web_session.add(ver)

    camp = Campaign(name="UI Campaign", message_template="Hello {{name}}", template_version_id=ver.id)
    web_session.add(camp)

    cont = Contact(name="UI Contact", phone_e164="+201000000000", country_code="20")
    web_session.add(cont)
    web_session.commit()

    # 1. Campaigns views
    resp = client.get("/campaigns", cookies=cookies)
    assert resp.status_code == 200
    assert "UI Campaign" in resp.text

    resp = client.get("/campaigns/new", cookies=cookies)
    assert resp.status_code == 200
    assert "Create New Campaign" in resp.text

    resp = client.get(f"/campaigns/{camp.id}", cookies=cookies)
    assert resp.status_code == 200
    assert "Campaign Contacts Breakdown" in resp.text

    # 2. Templates views
    resp = client.get("/templates", cookies=cookies)
    assert resp.status_code == 200
    assert "UI Template" in resp.text

    resp = client.get("/templates/new", cookies=cookies)
    assert resp.status_code == 200
    assert "Create Message Template" in resp.text

    resp = client.get(f"/templates/{tmpl.id}", cookies=cookies)
    assert resp.status_code == 200
    assert "Immutable Versioning" in resp.text

    # 3. Contacts views
    resp = client.get("/contacts", cookies=cookies)
    assert resp.status_code == 200
    assert "UI Contact" in resp.text

    resp = client.get("/contacts/import", cookies=cookies)
    assert resp.status_code == 200
    assert "Import Contacts (CSV)" in resp.text

    resp = client.get(f"/contacts/{cont.id}", cookies=cookies)
    assert resp.status_code == 200
    assert "Contact Overview" in resp.text
