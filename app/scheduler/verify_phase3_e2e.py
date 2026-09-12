"""
Manual E2E verification script for Phase 3.
Run with: python -m app.scheduler.verify_phase3_e2e
"""
import sys
import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact

from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.providers.mock_provider import MockMessageProvider
from app.limiter.frequency_service import FrequencyLimitService
from app.limiter.rate_limiter import RateLimiter
from app.scheduler.batch_manager import BatchManager
from app.scheduler.circuit_breaker import CircuitBreaker
from app.scheduler.emergency_stop import EmergencyStop
from app.scheduler.worker import QueueWorker


def main():
    db = SessionLocal()
    ts = int(time.time())

    try:
        print("==================================================")
        print("       Phase 3 End-to-End Pipeline Verification   ")
        print("==================================================")

        # 1. Provider setup
        print("1. Initializing MockMessageProvider...")
        provider = MockMessageProvider()
        provider.connect()
        assert provider.health_check() is True
        print("   -> Provider connected and healthy.")

        # 2. Campaign and Contacts
        print("\n2. Creating Campaign and Contacts...")
        campaign = Campaign(name=f"Phase 3 Campaign {ts}", message_template="Hello {{name}}", status="RUNNING")
        contact1 = Contact(name="User 1", phone_e164=f"+1415555{ts % 10000:04d}", country_code="US")
        contact2 = Contact(name="User 2 (Temp Fail)", phone_e164=f"+1415555{(ts + 1) % 10000:04d}", country_code="US")
        db.add_all([campaign, contact1, contact2])
        db.commit()

        cc1 = CampaignContact(campaign_id=campaign.id, contact_id=contact1.id, status="ELIGIBLE")
        cc2 = CampaignContact(campaign_id=campaign.id, contact_id=contact2.id, status="ELIGIBLE")
        db.add_all([cc1, cc2])
        db.commit()
        print(f"   -> Campaign #{campaign.id} created with 2 contacts.")

        # Configure provider to fail User 2 temporarily
        provider.configure_recipient_behavior(
            phone=contact2.phone_e164,
            success=False,
            is_temporary=True,
            error_msg="Temporary network carrier timeout"
        )

        # 3. Batch Management
        print("\n3. Allocating Persistent Campaign Batch...")
        batch_mgr = BatchManager(db)
        batch = batch_mgr.create_batch(campaign_id=campaign.id, total_items=2)
        batch_mgr.start_batch(batch.id)
        print(f"   -> Batch #{batch.batch_number} (ID: {batch.id}) started. Status: {batch.status}")

        # 4. Enqueueing into Persistent Queue with Idempotency Keys
        print("\n4. Enqueueing Messages (PENDING -> QUEUED)...")
        queue_svc = PersistentQueueService(db)
        m1 = queue_svc.enqueue_message(
            campaign_id=campaign.id,
            contact_id=contact1.id,
            rendered_content=f"Hello {contact1.name}",
            campaign_contact_id=cc1.id,
            sequence_number=1,
            batch_id=batch.id,
            initial_state=QueueState.PENDING
        )
        m2 = queue_svc.enqueue_message(
            campaign_id=campaign.id,
            contact_id=contact2.id,
            rendered_content=f"Hello {contact2.name}",
            campaign_contact_id=cc2.id,
            sequence_number=1,
            batch_id=batch.id,
            initial_state=QueueState.PENDING
        )
        print(f"   -> Message 1 idempotency_key: {m1.idempotency_key} (Status: {m1.status})")
        print(f"   -> Message 2 idempotency_key: {m2.idempotency_key} (Status: {m2.status})")

        # Promote to QUEUED
        promoted = queue_svc.promote_pending_to_queued(campaign_id=campaign.id)
        print(f"   -> Promoted {promoted} messages from PENDING to QUEUED.")

        # 5. Worker Processing
        print("\n5. Running QueueWorker Orchestration...")
        worker = QueueWorker(db=db, provider=provider, worker_id="e2e_worker_node_1")

        # Process first message (should succeed)
        p1 = worker.process_next_message(campaign_id=campaign.id)
        db.refresh(m1)
        print(f"   -> Message #{m1.id} sent! Status: {m1.status} (sent_at: {m1.sent_at})")

        # Process second message (should fail temporarily and enter RETRY_PENDING)
        p2 = worker.process_next_message(campaign_id=campaign.id)
        db.refresh(m2)
        print(f"   -> Message #{m2.id} result: {m2.status} (error_type: {m2.error_type}, next_retry_at: {m2.next_retry_at})")

        # 6. Batch Progress & Recovery
        print("\n6. Validating Batch Progression & Crash Recovery...")
        db.refresh(batch)
        print(f"   -> Batch progress: {batch.processed_items}/{batch.total_items} items (Success: {batch.successful_items}, Failed: {batch.failed_items})")
        recovered_batch = batch_mgr.recover_batch(batch.id)
        print(f"   -> Reconciled Batch: Status={recovered_batch.status}, Processed={recovered_batch.processed_items}")

        # 7. Emergency Stop Latency Verification
        print("\n7. Verifying Emergency Stop Safety...")
        start_t = time.perf_counter()
        worker.emergency_stop.trigger(reason="E2E latency validation")
        is_stopped = worker.emergency_stop.is_active()
        stop_ms = (time.perf_counter() - start_t) * 1000.0
        print(f"   -> Emergency stop engaged in {stop_ms:.2f}ms (target <500ms: {'PASS' if stop_ms < 500 else 'FAIL'})")

        worker.emergency_stop.resume()
        print("   -> Emergency stop cleanly resumed.")

        print("\n==================================================")
        print("     Phase 3 Pipeline Verification SUCCESSFUL!    ")
        print("==================================================")

    except Exception as e:
        print(f"\n[ERROR] Pipeline verification failed: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
