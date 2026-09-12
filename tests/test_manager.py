import pytest
from app.campaigns.manager import CampaignManager
from app.campaigns.exceptions import InvalidStateTransitionError, CampaignNotFoundError, CampaignError
from app.models.campaign import Campaign
from app.models.audit_log import AuditLog

def test_create_campaign(db_session):
    mgr = CampaignManager(db_session)
    c = mgr.create_campaign("Test Camp", "Hello {{name}}")
    assert c.id is not None
    assert c.status == "DRAFT"
    
    # Verify audit log
    log = db_session.query(AuditLog).filter_by(campaign_id=c.id, event_type="CAMPAIGN_CREATED").first()
    assert log is not None

def test_duplicate_campaign(db_session):
    mgr = CampaignManager(db_session)
    c1 = mgr.create_campaign("Original", "Hi")
    c2 = mgr.duplicate_campaign(c1.id, "Copy")
    assert c2.name == "Copy"
    assert c2.message_template == "Hi"
    assert c2.status == "DRAFT"

def test_state_transitions_valid(db_session):
    mgr = CampaignManager(db_session)
    c = mgr.create_campaign("Flow", "msg")
    c = mgr.transition_state(c.id, "SCHEDULED")
    assert c.status == "SCHEDULED"
    c = mgr.transition_state(c.id, "RUNNING")
    assert c.status == "RUNNING"
    c = mgr.transition_state(c.id, "PAUSED")
    assert c.status == "PAUSED"
    c = mgr.transition_state(c.id, "RUNNING")
    assert c.status == "RUNNING"
    c = mgr.transition_state(c.id, "COMPLETED")
    assert c.status == "COMPLETED"

def test_state_transitions_invalid(db_session):
    mgr = CampaignManager(db_session)
    c = mgr.create_campaign("Flow2", "msg")
    with pytest.raises(InvalidStateTransitionError):
        mgr.transition_state(c.id, "PAUSED") # DRAFT to PAUSED is invalid

def test_update_campaign(db_session):
    mgr = CampaignManager(db_session)
    c = mgr.create_campaign("UpdateMe", "msg")
    c2 = mgr.update_campaign(c.id, daily_limit=50, message_template="New {{name}}")
    assert c2.daily_limit == 50
    assert c2.message_template == "New {{name}}"

def test_delete_campaign(db_session):
    mgr = CampaignManager(db_session)
    c = mgr.create_campaign("DeleteMe", "msg")
    c_id = c.id
    mgr.delete_campaign(c.id)
    with pytest.raises(CampaignNotFoundError):
        mgr.get_campaign(c_id)
