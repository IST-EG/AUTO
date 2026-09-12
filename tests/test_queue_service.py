import pytest
from datetime import datetime, timezone, timedelta

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.queue.exceptions import DuplicateMessageError, InvalidQueueStateTransitionError


@pytest.fixture
def test_setup(db_session):
    campaign = Campaign(name="Queue Test Campaign", message_template="Hello {{name}}")
    contact = Contact(name="Jane Doe", phone_e164="+14155552671", country_code="US")
    db_session.add(campaign)
    db_session.add(contact)
    db_session.commit()

    cc = CampaignContact(campaign_id=campaign.id, contact_id=contact.id, status="ELIGIBLE")
    db_session.add(cc)
    db_session.commit()

    return campaign, contact, cc


def test_enqueue_pending_and_promotion(db_session, test_setup):
    campaign, contact, cc = test_setup
    svc = PersistentQueueService(db_session)

    # Enqueue as PENDING
    msg = svc.enqueue_message(
        campaign_id=campaign.id,
        contact_id=contact.id,
        rendered_content="Hello Jane",
        campaign_contact_id=cc.id,
        sequence_number=1,
        initial_state=QueueState.PENDING
    )

    assert msg.id is not None
    assert msg.status == QueueState.PENDING
    assert msg.idempotency_key == f"cc_{cc.id}_seq_1"

    # Promote to QUEUED
    promoted = svc.promote_pending_to_queued(campaign_id=campaign.id)
    assert promoted == 1

    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED


def test_enqueue_idempotency_enforcement(db_session, test_setup):
    campaign, contact, cc = test_setup
    svc = PersistentQueueService(db_session)

    # First enqueue succeeds
    msg1 = svc.enqueue_message(
        campaign_id=campaign.id,
        contact_id=contact.id,
        rendered_content="Hello Jane",
        campaign_contact_id=cc.id,
        sequence_number=1
    )
    assert msg1.id is not None

    # Duplicate enqueue with exact same sequence/campaign_contact raises DuplicateMessageError
    with pytest.raises(DuplicateMessageError):
        svc.enqueue_message(
            campaign_id=campaign.id,
            contact_id=contact.id,
            rendered_content="Hello Jane again",
            campaign_contact_id=cc.id,
            sequence_number=1
        )


def test_atomic_claim_and_lease(db_session, test_setup):
    campaign, contact, cc = test_setup
    svc = PersistentQueueService(db_session)

    msg = svc.enqueue_message(
        campaign_id=campaign.id,
        contact_id=contact.id,
        rendered_content="Hello Jane",
        campaign_contact_id=cc.id,
        initial_state=QueueState.QUEUED
    )

    # Worker 1 claims
    claimed = svc.claim_next_message(worker_id="worker_1", campaign_id=campaign.id)
    assert claimed is not None
    assert claimed.id == msg.id
    assert claimed.status == QueueState.PROCESSING
    assert claimed.locked_by == "worker_1"
    assert claimed.locked_at is not None
    assert claimed.attempt_count == 1

    # Worker 2 attempts to claim same queue — should find nothing
    claimed_again = svc.claim_next_message(worker_id="worker_2", campaign_id=campaign.id)
    assert claimed_again is None


def test_stale_lease_recovery(db_session, test_setup):
    campaign, contact, cc = test_setup
    svc = PersistentQueueService(db_session)

    msg = svc.enqueue_message(
        campaign_id=campaign.id,
        contact_id=contact.id,
        rendered_content="Hello Jane",
        campaign_contact_id=cc.id,
        initial_state=QueueState.QUEUED
    )

    claimed = svc.claim_next_message(worker_id="crashed_worker", campaign_id=campaign.id)
    assert claimed is not None

    # Manually simulate stale lease from 1 hour ago
    claimed.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    # Run recovery with lease timeout of 120s
    recovered = svc.recover_stale_leases(lease_timeout_seconds=120)
    assert recovered == 1

    db_session.refresh(claimed)
    assert claimed.status == QueueState.RETRY_PENDING
    assert claimed.locked_by is None
    assert claimed.locked_at is None
    assert claimed.error_type == "STALE_LEASE_RECOVERED"


def test_stale_lease_exhaustion(db_session, test_setup):
    campaign, contact, cc = test_setup
    svc = PersistentQueueService(db_session)

    msg = svc.enqueue_message(
        campaign_id=campaign.id,
        contact_id=contact.id,
        rendered_content="Hello Jane",
        campaign_contact_id=cc.id,
        max_attempts=1,
        initial_state=QueueState.QUEUED
    )

    claimed = svc.claim_next_message(worker_id="crashed_worker", campaign_id=campaign.id)
    assert claimed.attempt_count == 1

    # Simulate expired lease
    claimed.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    # Recovery should mark FAILED because attempt_count >= max_attempts
    recovered = svc.recover_stale_leases(lease_timeout_seconds=120)
    assert recovered == 1

    db_session.refresh(claimed)
    assert claimed.status == QueueState.FAILED
    assert claimed.error_type == "STALE_LEASE_EXHAUSTED"


def test_queue_metrics(db_session, test_setup):
    campaign, contact, cc = test_setup
    svc = PersistentQueueService(db_session)

    svc.enqueue_message(campaign.id, contact.id, "M1", custom_idempotency_key="k1", initial_state=QueueState.PENDING)
    svc.enqueue_message(campaign.id, contact.id, "M2", custom_idempotency_key="k2", initial_state=QueueState.QUEUED)

    metrics = svc.get_queue_metrics(campaign.id)
    assert metrics[QueueState.PENDING] == 1
    assert metrics[QueueState.QUEUED] == 1
    assert metrics[QueueState.SENT] == 0
