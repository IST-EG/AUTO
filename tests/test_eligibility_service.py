import pytest
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.campaigns.eligibility_service import CampaignEligibilityService

def test_eligibility_invalid_phone(db_session):
    svc = CampaignEligibilityService(db_session)
    campaign = Campaign(id=1, name="C1")
    contact = Contact(id=1, name="A", contact_status="inactive")
    res = svc.check_eligibility(contact, campaign)
    assert not res.eligible
    assert res.reason == "INVALID_PHONE"

def test_eligibility_opted_out(db_session):
    svc = CampaignEligibilityService(db_session)
    campaign = Campaign(id=1, name="C1")
    contact = Contact(id=1, name="A", contact_status="active", consent_status="opted_out")
    res = svc.check_eligibility(contact, campaign)
    assert not res.eligible
    assert res.reason == "OPTED_OUT"

def test_eligibility_duplicate(db_session):
    svc = CampaignEligibilityService(db_session)
    campaign = Campaign(id=1, name="C1")
    contact = Contact(id=1, name="A", contact_status="duplicate")
    res = svc.check_eligibility(contact, campaign)
    assert not res.eligible
    assert res.reason == "DUPLICATE"

def test_eligibility_already_contacted(db_session):
    svc = CampaignEligibilityService(db_session)
    campaign = Campaign(name="C1", message_template="Hello")
    contact = Contact(name="A", phone_e164="+12025550119", country_code="US")
    db_session.add(campaign)
    db_session.add(contact)
    db_session.commit()
    
    cc = CampaignContact(campaign_id=campaign.id, contact_id=contact.id, status="SENT")
    db_session.add(cc)
    db_session.commit()
    
    res = svc.check_eligibility(contact, campaign)
    assert not res.eligible
    assert res.reason == "ALREADY_CONTACTED"

def test_eligibility_success(db_session):
    svc = CampaignEligibilityService(db_session)
    campaign = Campaign(name="C2", message_template="Hello")
    contact = Contact(name="A", phone_e164="+12025550120", country_code="US")
    db_session.add(campaign)
    db_session.add(contact)
    db_session.commit()
    
    res = svc.check_eligibility(contact, campaign)
    assert res.eligible
    assert res.reason == "ELIGIBLE"
