from unittest.mock import MagicMock
import pytest
from datetime import datetime, timezone

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message

from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.scheduler.worker import QueueWorker
from app.providers.whatsapp_web.provider import WhatsAppWebProvider
from app.providers.whatsapp_web.state import WhatsAppSessionState
from app.providers.whatsapp_web.exceptions import (
    WhatsAppInvalidNumberError,
    WhatsAppSendTimeoutError,
    WhatsAppBrowserCrashError,
)


@pytest.fixture
def whatsapp_worker_setup(db_session):
    camp = Campaign(name="WA Worker Camp", message_template="Hello {{name}}", status="RUNNING", error_threshold=3)
    contact = Contact(name="Bob", phone_e164="+14155552675", country_code="US")
    db_session.add_all([camp, contact])
    db_session.commit()

    cc = CampaignContact(campaign_id=camp.id, contact_id=contact.id, status="ELIGIBLE")
    db_session.add(cc)
    db_session.commit()

    # Mocked session manager with responsive browser
    sm = MagicMock()
    sm.state = WhatsAppSessionState.CONNECTED
    sm.check_health.return_value = True
    sm.browser = MagicMock()
    sm.browser.is_alive.return_value = True
    sm.browser.is_invalid_phone_dialog_present.return_value = False
    sm.browser.wait_for_send_confirmation.return_value = True
    sm.browser.capture_diagnostic_snippet.return_value = "Diagnostic details"

    provider = WhatsAppWebProvider(session_manager=sm)
    worker = QueueWorker(db=db_session, provider=provider, worker_id="wa_worker_test")

    return camp, contact, cc, sm, provider, worker


def test_worker_with_whatsapp_provider_success(db_session, whatsapp_worker_setup):
    camp, contact, cc, sm, provider, worker = whatsapp_worker_setup
    q_svc = PersistentQueueService(db_session)

    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Bob",
        campaign_contact_id=cc.id,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None
    assert processed.id == msg.id

    db_session.refresh(msg)
    assert msg.status == QueueState.SENT
    assert msg.sent_at is not None

    db_session.refresh(contact)
    assert contact.messages_sent_count == 1

    db_session.refresh(cc)
    assert cc.status == "SENT"


def test_worker_with_whatsapp_provider_invalid_number(db_session, whatsapp_worker_setup):
    camp, contact, cc, sm, provider, worker = whatsapp_worker_setup
    # Simulate invalid number popup
    sm.browser.is_invalid_phone_dialog_present.return_value = True

    q_svc = PersistentQueueService(db_session)
    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Bob",
        campaign_contact_id=cc.id,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None

    db_session.refresh(msg)
    assert msg.status == QueueState.FAILED
    assert msg.error_type == "PERMANENT"
    assert "PERMANENT" in msg.last_error


def test_worker_with_whatsapp_provider_timeout_retry(db_session, whatsapp_worker_setup):
    camp, contact, cc, sm, provider, worker = whatsapp_worker_setup
    # Simulate send confirmation timeout
    sm.browser.wait_for_send_confirmation.return_value = False

    q_svc = PersistentQueueService(db_session)
    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Bob",
        campaign_contact_id=cc.id,
        max_attempts=3,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None

    db_session.refresh(msg)
    assert msg.status == QueueState.RETRY_PENDING
    assert msg.error_type == "TEMPORARY"
    assert msg.attempt_count == 1
    assert msg.next_retry_at is not None


def test_worker_with_whatsapp_provider_unknown_outcome_no_retry(db_session, whatsapp_worker_setup):
    camp, contact, cc, sm, provider, worker = whatsapp_worker_setup
    # Simulate crash right after send action was clicked
    sm.browser.wait_for_send_confirmation.side_effect = WhatsAppBrowserCrashError("Chrome killed mid-confirmation")

    q_svc = PersistentQueueService(db_session)
    msg = q_svc.enqueue_message(
        campaign_id=camp.id,
        contact_id=contact.id,
        rendered_content="Hello Bob",
        campaign_contact_id=cc.id,
        max_attempts=3,
        initial_state=QueueState.QUEUED
    )

    processed = worker.process_next_message(campaign_id=camp.id)
    assert processed is not None

    db_session.refresh(msg)
    # CRITICAL: Must be marked FAILED rather than RETRY_PENDING to prevent blind duplicate delivery
    assert msg.status == QueueState.FAILED
    assert "UNKNOWN_OUTCOME" in msg.last_error
