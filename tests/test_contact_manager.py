import pytest
from app.campaigns.contact_manager import CampaignContactManager
from app.campaigns.manager import CampaignManager
from app.contacts.manager import ContactManager
from app.campaigns.exceptions import CampaignContactError
from app.models.campaign_contact import CampaignContact
from app.models.audit_log import AuditLog

def test_add_multiple_contacts(db_session):
    camp_mgr = CampaignManager(db_session)
    contact_mgr = ContactManager(db_session)
    cc_mgr = CampaignContactManager(db_session)
    
    c = camp_mgr.create_campaign("CC Test", "Hi")
    c1 = contact_mgr.create_contact("A", "+12025550111", "US")
    c2 = contact_mgr.create_contact("B", "+12025550112", "US", consent_status="opted_out")
    
    res = cc_mgr.add_multiple_contacts(c, [c1, c2])
    assert res["total"] == 2
    assert res["added"] == 1
    assert res["excluded"] == 1
    
    # Check db
    ccs = db_session.query(CampaignContact).filter_by(campaign_id=c.id).all()
    assert len(ccs) == 2
    
    status_map = {cc.contact_id: cc.status for cc in ccs}
    assert status_map[c1.id] == "ELIGIBLE"
    assert status_map[c2.id] == "EXCLUDED"

def test_prevent_duplicates_in_campaign(db_session):
    camp_mgr = CampaignManager(db_session)
    contact_mgr = ContactManager(db_session)
    cc_mgr = CampaignContactManager(db_session)
    
    c = camp_mgr.create_campaign("CC Test 2", "Hi")
    c1 = contact_mgr.create_contact("C", "+12025550113", "US")
    
    cc_mgr.add_contact(c, c1)
    
    with pytest.raises(CampaignContactError):
        cc_mgr.add_contact(c, c1)

def test_remove_contact(db_session):
    camp_mgr = CampaignManager(db_session)
    contact_mgr = ContactManager(db_session)
    cc_mgr = CampaignContactManager(db_session)
    
    c = camp_mgr.create_campaign("CC Test 3", "Hi")
    c1 = contact_mgr.create_contact("D", "+12025550114", "US")
    
    cc_mgr.add_contact(c, c1)
    assert cc_mgr.remove_contact(c.id, c1.id) is True
    
    ccs = db_session.query(CampaignContact).filter_by(campaign_id=c.id).all()
    assert len(ccs) == 0
