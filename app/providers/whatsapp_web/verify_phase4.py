"""
Phase 4 Verification Script.

Demonstrates WhatsAppWebProvider integration, session state machine transitions,
error mapper contracts, and safe queue worker integration.
Run with: python -m app.providers.whatsapp_web.verify_phase4
"""

import sys
import time
from unittest.mock import MagicMock

from app.database import SessionLocal
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact

from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.scheduler.worker import QueueWorker

from app.providers.base import ProviderMessage
from app.providers.whatsapp_web.provider import WhatsAppWebProvider
from app.providers.whatsapp_web.session_manager import WhatsAppSessionManager
from app.providers.whatsapp_web.state import WhatsAppSessionState
from app.providers.whatsapp_web.error_mapper import WhatsAppErrorMapper, ErrorCategory
from app.providers.whatsapp_web.exceptions import (
    WhatsAppInvalidNumberError,
    WhatsAppSendTimeoutError,
    WhatsAppBrowserCrashError,
)


def main():
    db = SessionLocal()
    ts = int(time.time())

    try:
        print("==================================================")
        print("    Phase 4: WhatsApp Web Provider Verification   ")
        print("==================================================")

        # 1. Verify Error Mapper Contract
        print("\n1. Testing WhatsAppErrorMapper Contract...")
        # A: Invalid number -> PERMANENT
        e1 = WhatsAppErrorMapper.classify(exception=WhatsAppInvalidNumberError("No WA account"))
        print(f"   -> Invalid Number: Category={e1.category}, is_temporary={e1.is_temporary}")
        assert e1.category == ErrorCategory.PERMANENT and e1.is_temporary is False

        # B: Send confirmation timeout -> TEMPORARY
        e2 = WhatsAppErrorMapper.classify(exception=WhatsAppSendTimeoutError("Timeout"))
        print(f"   -> Confirmation Timeout: Category={e2.category}, is_temporary={e2.is_temporary}")
        assert e2.category == ErrorCategory.TEMPORARY and e2.is_temporary is True

        # C: Crash after send action attempted -> UNKNOWN_OUTCOME (No blind retry!)
        e3 = WhatsAppErrorMapper.classify(
            exception=WhatsAppBrowserCrashError("Chrome died mid-confirmation"),
            send_action_attempted=True
        )
        print(f"   -> Post-Send Crash: Category={e3.category}, is_temporary={e3.is_temporary} (Safe no-retry)")
        assert e3.category == ErrorCategory.UNKNOWN_OUTCOME and e3.is_temporary is False

        # 2. Session Manager Mock State Transitions
        print("\n2. Testing WhatsAppSessionManager Lifecycle...")
        mock_browser = MagicMock()
        mock_browser.is_alive.return_value = True
        mock_browser.is_chat_ready.return_value = False
        mock_browser.is_qr_present.return_value = True

        sm = WhatsAppSessionManager(browser=mock_browser)
        state1 = sm.initialize_session()
        print(f"   -> Initial state (QR present): {state1}")
        assert state1 == WhatsAppSessionState.AUTHENTICATING

        # Simulate QR scan (QR disappears, chat becomes ready)
        mock_browser.is_qr_present.return_value = False
        mock_browser.is_chat_ready.return_value = True
        authenticated = sm.await_authentication(timeout=2.0)
        print(f"   -> Operator scanned QR: Authenticated={authenticated}, State={sm.state}")
        assert sm.state == WhatsAppSessionState.CONNECTED

        # Health check
        print(f"   -> Health check status: {sm.check_health()}")
        assert sm.check_health() is True

        # 3. WhatsAppWebProvider & QueueWorker Integration
        print("\n3. Testing WhatsAppWebProvider & QueueWorker Integration...")
        mock_browser.is_invalid_phone_dialog_present.return_value = False
        mock_browser.wait_for_send_confirmation.return_value = True
        mock_browser.capture_diagnostic_snippet.return_value = "WhatsApp Web Main Chat View"

        provider = WhatsAppWebProvider(session_manager=sm)
        assert provider.health_check() is True

        # Seed test campaign & contact
        campaign = Campaign(name=f"Phase 4 WA Campaign {ts}", message_template="Hello {{name}}", status="RUNNING")
        contact = Contact(name="WhatsApp Lead", phone_e164=f"+1415555{ts % 10000:04d}", country_code="US")
        db.add_all([campaign, contact])
        db.commit()

        cc = CampaignContact(campaign_id=campaign.id, contact_id=contact.id, status="ELIGIBLE")
        db.add(cc)
        db.commit()

        # Enqueue message
        q_svc = PersistentQueueService(db)
        msg = q_svc.enqueue_message(
            campaign_id=campaign.id,
            contact_id=contact.id,
            rendered_content=f"Hello {contact.name}, welcome to WhatsApp outreach!",
            campaign_contact_id=cc.id,
            initial_state=QueueState.QUEUED
        )
        print(f"   -> Message #{msg.id} enqueued (idempotency_key={msg.idempotency_key})")

        # Worker processes message via WhatsAppWebProvider
        worker = QueueWorker(db=db, provider=provider, worker_id="wa_e2e_worker")
        processed = worker.process_next_message(campaign_id=campaign.id)
        assert processed is not None

        db.refresh(msg)
        print(f"   -> Message status after worker dispatch: {msg.status} (sent_at={msg.sent_at})")
        assert msg.status == QueueState.SENT

        # 4. Clean shutdown
        print("\n4. Shutting down provider session...")
        provider.disconnect()
        print(f"   -> Session manager state: {sm.state}")
        assert sm.state == WhatsAppSessionState.STOPPED

        print("\n==================================================")
        print("     Phase 4 Verification Script SUCCESSFUL!      ")
        print("==================================================")

    except Exception as e:
        print(f"\n[ERROR] Phase 4 verification failed: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
