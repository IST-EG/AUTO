import pytest
from datetime import datetime, timezone, timedelta

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_batch import CampaignBatch
from app.models.app_setting import AppSetting

from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.queue.exceptions import (
    MessageNotFoundError,
    InvalidQueueStateTransitionError,
    DuplicateMessageError
)

from app.scheduler.batch_manager import BatchManager
from app.scheduler.worker import QueueWorker
from app.providers.mock_provider import MockMessageProvider
from app.limiter.frequency_service import FrequencyLimitService


@pytest.fixture
def edge_setup(db_session):
    camp = Campaign(name="Edge Camp", message_template="Hello {{name}}", status="RUNNING", daily_limit=10)
    contact = Contact(name="Edge Contact", phone_e164="+14155552699", country_code="US")
    db_session.add_all([camp, contact])
    db_session.commit()
    return camp, contact


def test_queue_service_error_paths(db_session, edge_setup):
    camp, contact = edge_setup
    svc = PersistentQueueService(db_session)

    # Invalid initial state
    with pytest.raises(InvalidQueueStateTransitionError):
        svc.enqueue_message(camp.id, contact.id, "Hi", initial_state=QueueState.SENT)

    # Message not found errors
    with pytest.raises(MessageNotFoundError):
        svc.mark_sent(999999, "w1")

    with pytest.raises(MessageNotFoundError):
        svc.mark_failed(999999, "w1", "err")

    with pytest.raises(MessageNotFoundError):
        svc.mark_retry(999999, "w1", "err", 10.0)

    with pytest.raises(MessageNotFoundError):
        svc.mark_skipped(999999, "w1", "reason")

    with pytest.raises(MessageNotFoundError):
        svc.cancel_message(999999)

    # Cancel message valid flow
    msg = svc.enqueue_message(camp.id, contact.id, "Hi", initial_state=QueueState.PENDING)
    cancelled = svc.cancel_message(msg.id)
    assert cancelled.status == QueueState.CANCELLED

    # Direct mark_skipped
    msg2 = svc.enqueue_message(camp.id, contact.id, "Hi2", custom_idempotency_key="direct_skip", initial_state=QueueState.QUEUED)
    claimed2 = svc.claim_next_message("w1", camp.id)
    skipped = svc.mark_skipped(claimed2.id, "w1", "Opted out")
    assert skipped.status == QueueState.SKIPPED
    assert skipped.last_error == "Opted out"


def test_batch_manager_pause_resume_and_active(db_session, edge_setup):
    camp, _ = edge_setup
    bm = BatchManager(db_session)

    # Error on missing batch
    with pytest.raises(ValueError):
        bm.start_batch(999999)

    with pytest.raises(ValueError):
        bm.record_item_result(999999, success=True)

    with pytest.raises(ValueError):
        bm.pause_batch(999999)

    with pytest.raises(ValueError):
        bm.resume_batch(999999)

    with pytest.raises(ValueError):
        bm.recover_batch(999999)

    # Valid pause & resume
    batch = bm.create_batch(camp.id, total_items=5)
    bm.start_batch(batch.id)

    paused = bm.pause_batch(batch.id)
    assert paused.status == "PAUSED"

    resumed = bm.resume_batch(batch.id)
    assert resumed.status == "IN_PROGRESS"

    active = bm.get_active_batch(camp.id)
    assert active is not None
    assert active.id == batch.id


def test_mock_provider_lifecycle():
    p = MockMessageProvider(simulated_latency=0.01)
    # Not connected
    from app.providers.base import ProviderMessage
    msg = ProviderMessage(1, "k", "+12345", "hi")
    res = p.send_message(msg)
    assert not res.success
    assert "not connected" in res.error_message
    assert res.is_temporary_error is True

    # Connect & health check
    p.connect()
    assert p.health_check() is True

    res2 = p.send_message(msg)
    assert res2.success is True

    # Disconnect
    p.disconnect()
    assert p.health_check() is False


def test_frequency_service_30d_limit(db_session, edge_setup):
    camp, contact = edge_setup
    svc = FrequencyLimitService(db_session)

    # Set 30d limit to 2
    db_session.add(AppSetting(key="freq_max_messages_per_day", value="10"))
    db_session.add(AppSetting(key="freq_max_messages_per_campaign", value="10"))
    db_session.add(AppSetting(key="freq_max_messages_30d", value="2"))
    db_session.commit()

    from app.models.message import Message
    now = datetime.now(timezone.utc)
    m1 = Message(campaign_id=camp.id, contact_id=contact.id, rendered_content="1", idempotency_key="f30_1", status="SENT", sent_at=now - timedelta(days=2))
    m2 = Message(campaign_id=camp.id, contact_id=contact.id, rendered_content="2", idempotency_key="f30_2", status="SENT", sent_at=now - timedelta(days=1))
    db_session.add_all([m1, m2])
    db_session.commit()

    res = svc.check_frequency(contact, camp)
    assert not res.eligible
    assert res.reason == "CROSS_CAMPAIGN_30D_LIMIT_REACHED"
    assert res.next_allowed_at is not None


def test_worker_quota_and_frequency_skips(db_session, edge_setup):
    camp, contact = edge_setup
    q_svc = PersistentQueueService(db_session)
    provider = MockMessageProvider()
    provider.connect()
    worker = QueueWorker(db_session, provider, worker_id="w_quota")

    # 1. Frequency skip in worker
    # Put contact over daily limit
    from app.models.message import Message
    m_prior = Message(campaign_id=camp.id, contact_id=contact.id, rendered_content="prior", idempotency_key="pr_1", status="SENT", sent_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    db_session.add(m_prior)
    db_session.commit()

    bm = BatchManager(db_session)
    batch = bm.create_batch(camp.id, total_items=1)
    bm.start_batch(batch.id)

    msg = q_svc.enqueue_message(camp.id, contact.id, "Hello", custom_idempotency_key="skip_w1", initial_state=QueueState.QUEUED, batch_id=batch.id)

    processed = worker.process_next_message(camp.id)
    assert processed is not None
    db_session.refresh(msg)
    assert msg.status == QueueState.SKIPPED
    assert "Frequency limit" in msg.last_error


def test_worker_quota_pause(db_session, edge_setup):
    camp, contact = edge_setup
    other_contact = Contact(name="Other", phone_e164="+14155552698", country_code="US")
    db_session.add(other_contact)
    db_session.commit()

    q_svc = PersistentQueueService(db_session)
    provider = MockMessageProvider()
    provider.connect()
    worker = QueueWorker(db_session, provider, worker_id="w_quota2")

    # Set campaign daily limit to 1
    camp.daily_limit = 1
    # Allow high frequency limits
    db_session.add(AppSetting(key="freq_max_messages_per_day", value="10"))
    from app.models.message import Message
    m_prior = Message(campaign_id=camp.id, contact_id=other_contact.id, rendered_content="p", idempotency_key="pr_2", status="SENT", sent_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    db_session.add(m_prior)
    db_session.commit()

    msg = q_svc.enqueue_message(camp.id, contact.id, "Hello", custom_idempotency_key="quota_w1", initial_state=QueueState.QUEUED)

    processed = worker.process_next_message(camp.id)
    assert processed is None
    # Message was released back to QUEUED
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert msg.locked_at is None


def test_worker_global_quota_pause(db_session, edge_setup):
    camp, contact = edge_setup
    other_contact = Contact(name="Other2", phone_e164="+14155552697", country_code="US")
    db_session.add(other_contact)
    db_session.commit()

    q_svc = PersistentQueueService(db_session)
    provider = MockMessageProvider()
    provider.connect()
    worker = QueueWorker(db_session, provider, worker_id="w_global_quota")

    # Global daily limit = 1
    db_session.add(AppSetting(key="global_daily_limit", value="1"))
    db_session.add(AppSetting(key="freq_max_messages_per_day", value="10"))
    from app.models.message import Message
    m_prior = Message(campaign_id=camp.id, contact_id=other_contact.id, rendered_content="p", idempotency_key="pr_3", status="SENT", sent_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    db_session.add(m_prior)
    db_session.commit()

    msg = q_svc.enqueue_message(camp.id, contact.id, "Hello", custom_idempotency_key="gquota_w1", initial_state=QueueState.QUEUED)

    processed = worker.process_next_message(camp.id)
    assert processed is None
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED


def test_worker_emergency_stop_between_claim_and_send(db_session, edge_setup):
    camp, contact = edge_setup
    q_svc = PersistentQueueService(db_session)
    provider = MockMessageProvider()
    provider.connect()
    worker = QueueWorker(db_session, provider, worker_id="w_estop")

    msg = q_svc.enqueue_message(camp.id, contact.id, "Hello", custom_idempotency_key="estop_w1", initial_state=QueueState.QUEUED)

    # Monkeypatch emergency stop to trigger after claiming
    orig_claim = q_svc.claim_next_message
    def mock_claim(*args, **kwargs):
        claimed = orig_claim(*args, **kwargs)
        worker.emergency_stop.trigger(reason="Triggered mid-claim")
        return claimed

    worker.queue_service.claim_next_message = mock_claim

    processed = worker.process_next_message(camp.id)
    assert processed is None

    # Message released back to QUEUED
    db_session.refresh(msg)
    assert msg.status == QueueState.QUEUED
    assert msg.locked_at is None
