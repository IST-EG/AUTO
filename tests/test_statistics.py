import pytest
from app.campaigns.manager import CampaignManager
from app.contacts.manager import ContactManager
from app.campaigns.contact_manager import CampaignContactManager
from app.campaigns.statistics_service import CampaignStatisticsService
from app.models.campaign_contact import CampaignContact

def test_campaign_statistics(db_session):
    camp_mgr = CampaignManager(db_session)
    contact_mgr = ContactManager(db_session)
    cc_mgr = CampaignContactManager(db_session)
    stat_svc = CampaignStatisticsService(db_session)
    
    c = camp_mgr.create_campaign("Stats Test", "Hi")
    
    # 1 eligible
    c1 = contact_mgr.create_contact("S1", "+12025550115", "US")
    # 1 excluded (opt_out)
    c2 = contact_mgr.create_contact("S2", "+12025550116", "US", consent_status="opted_out")
    
    cc_mgr.add_multiple_contacts(c, [c1, c2])
    
    # Manually add some other statuses for testing
    c3 = contact_mgr.create_contact("S3", "+12025550117", "US")
    cc3 = cc_mgr.add_contact(c, c3)
    cc3.status = "SENT"
    
    c4 = contact_mgr.create_contact("S4", "+12025550118", "US")
    cc4 = cc_mgr.add_contact(c, c4)
    cc4.status = "FAILED"
    
    db_session.commit()
    
    stats = stat_svc.get_statistics(c.id)
    assert stats["total_contacts"] == 4
    assert stats["eligible"] == 1
    assert stats["excluded"] == 1
    assert stats["sent"] == 1
    assert stats["failed"] == 1
    assert stats["skipped"] == 0
    assert stats["pending"] == 0
    
    # processed = 1(excluded) + 1(sent) + 1(failed) = 3
    # 3 / 4 = 75.0%
    assert stats["completion_percentage"] == 75.0
