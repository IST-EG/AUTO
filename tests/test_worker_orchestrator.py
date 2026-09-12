import pytest
from datetime import datetime, timezone, timedelta

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message

from app.providers.mock_provider import MockMessageProvider
from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.scheduler.worker import QueueWorker


@pytest.fixture
def worker_setup(db_session):
    camp = Campaign(name="Worker Test Camp", message_template="Hello {{name}}", status="RUNNING")
    contact = Contact(name="Sam", phone_e164="+14155552673", country_code="US")
    db_session.add_all([camp, contact])
    db_session.commit()

    cc = CampaignContact(campaign_id=camp.id, contact_id=contact.id, status="ELIGIBLE")
    db_session.add(cc)
    db_session.commit()

    provider = MockMessageProvider()
    provider.connect()

    worker = QueueWorker(db=db_session, provider=provider, worker_id="test_worker_1")
    return camp, contact, cc, provider, worker


def test_worker_process_successful_send(db_session, worker_setup):
    camp, contact, cc, provider, worker = worker_setup
    q_svc = PersistentQueueService(db_session)

    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Sam",
        campaign_contact_id=cc.id,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None
    assert processed.id == msg.id

    db_session.refresh(msg)
    assert msg.status == QueueState.SENT
    assert msg.sent_at is not None
    assert len(provider.sent_messages) == 1
    assert provider.sent_messages[0].recipient_phone == "+14155552673"

    # Contact stats updated
    db_session.refresh(contact)
    assert contact.messages_sent_count == 1
    assert contact.last_contacted_at is not None

    # CampaignContact status updated
    db_session.refresh(cc)
    assert cc.status == "SENT"


def test_worker_temporary_failure_retry(db_session, worker_setup):
    camp, contact, cc, provider, worker = worker_setup
    q_svc = PersistentQueueService(db_session)

    # Configure provider to fail temporarily
    provider.configure_recipient_behavior(
        phone=contact.phone_e164,
        success=False,
        is_temporary=True,
        error_msg="Temporary network glitch"
    )

    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Sam",
        campaign_contact_id=cc.id,
        max_attempts=3,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None

    db_session.refresh(msg)
    assert msg.status == QueueState.RETRY_PENDING
    assert msg.attempt_count == 1
    assert msg.error_type == "TEMPORARY"
    assert msg.next_retry_at is not None
    assert "Temporary network glitch" in msg.last_error


def test_worker_permanent_failure(db_session, worker_setup):
    camp, contact, cc, provider, worker = worker_setup
    q_svc = PersistentQueueService(db_session)

    # Configure provider to fail permanently
    provider.configure_recipient_behavior(
        phone=contact.phone_e164,
        success=False,
        is_temporary=False,
        error_msg="Number is not registered on platform"
    )

    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Sam",
        campaign_contact_id=cc.id,
        max_attempts=3,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None

    db_session.refresh(msg)
    assert msg.status == QueueState.FAILED
    assert msg.attempt_count == 1
    assert msg.error_type == "PERMANENT"
    assert "Number is not registered" in msg.last_error

    db_session.refresh(cc)
    assert cc.status == "FAILED"


def test_worker_halts_on_emergency_stop(db_session, worker_setup):
    camp, contact, cc, provider, worker = worker_setup
    q_svc = PersistentQueueService(db_session)

    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Sam",
        campaign_contact_id=cc.id,
        initial_state=QueueState.QUEUED
    )

    # Trigger emergency stop
    worker.emergency_stop.trigger(reason="Test stop")

    # Worker attempt
    result = worker.process_next_message(campaign_id=camp.id)
    assert result is None

    # Message must remain safe in QUEUED state
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert len(provider.sent_messages) == 0
