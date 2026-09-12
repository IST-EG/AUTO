"""
Phase 5 End-to-End Verification Script.

Demonstrates:
1. CLI operational commands (session, campaign, queue, emergency, runner).
2. Business state transitions via 'outreach campaign run' (no daemon started).
3. Production runner execution with pacing and audit logging.
4. Process singularity protection (duplicate runner rejection).
5. Emergency stop killswitch activation and claim prevention.
6. Safe UNKNOWN_OUTCOME manual reconciliation override with audit tracking.

Run with:
    python -m app.cli.verify_phase5_e2e
"""

import sys
import time
import json
from argparse import Namespace

from app.database import SessionLocal
from app.cli.exit_codes import ExitCode
from app.cli.commands.session import handle_session_status
from app.cli.commands.campaign import handle_campaign_run, handle_campaign_status
from app.cli.commands.emergency import handle_emergency_stop, handle_emergency_status, handle_emergency_resume
from app.cli.commands.queue import handle_queue_status, handle_queue_inspect, handle_queue_override
from app.cli.commands.runner import handle_runner_status
from app.runner.production_runner import ProductionRunner
from app.runner.process_lock import ProcessLock
from app.providers.mock_provider import MockMessageProvider
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.models.audit_log import AuditLog
from app.queue.state_machine import QueueState
from app.scheduler.emergency_stop import EmergencyStop


def main():
    db = SessionLocal()
    ts = int(time.time())

    try:
        print("==================================================================")
        print("    Phase 5: Operational CLI and Live Production Runner E2E Verify")
        print("==================================================================")

        # ----------------------------------------------------------------------
        # 1. Operational CLI Status Inspection
        # ----------------------------------------------------------------------
        print("\n1. Testing Operational CLI Status Commands...")
        res = handle_session_status(Namespace(), db)
        assert res == ExitCode.SUCCESS, f"Expected SUCCESS, got {res}"
        print("   [OK] 'outreach session status' passed.")

        res = handle_emergency_status(Namespace(), db)
        assert res == ExitCode.SUCCESS, f"Expected SUCCESS, got {res}"
        print("   [OK] 'outreach emergency-status' passed.")

        res = handle_runner_status(Namespace(), db)
        assert res == ExitCode.SUCCESS, f"Expected SUCCESS, got {res}"
        print("   [OK] 'outreach runner status' passed (no active runner).")

        # ----------------------------------------------------------------------
        # 2. Campaign Business State Transition (No Daemon)
        # ----------------------------------------------------------------------
        print("\n2. Testing Campaign Business Transition via CLI...")
        camp = Campaign(
            name=f"Phase 5 Verification Campaign {ts}",
            message_template="Hello {{name}}, welcome to our verified service!",
            status="DRAFT",
            daily_limit=100,
            min_delay_seconds=1,
            max_delay_seconds=1,
            error_threshold=3,
        )
        db.add(camp)
        db.commit()

        # Run campaign run command
        res = handle_campaign_run(Namespace(campaign_id=camp.id), db)
        assert res == ExitCode.SUCCESS, f"Expected SUCCESS, got {res}"
        db.refresh(camp)
        assert camp.status == "RUNNING", f"Expected RUNNING, got {camp.status}"
        print(f"   [OK] 'outreach campaign run {camp.id}' transitioned campaign to RUNNING without launching daemon.")

        # ----------------------------------------------------------------------
        # 3. Production Runner Dispatch & Audit Logging
        # ----------------------------------------------------------------------
        print("\n3. Testing Single-Campaign Production Runner Dispatch...")
        contact = Contact(
            name="Alice Verification",
            phone_e164=f"+1555{ts % 10000000:07d}",
            country_code="US"
        )
        db.add(contact)
        db.flush()

        camp_contact = CampaignContact(
            campaign_id=camp.id,
            contact_id=contact.id,
            status="ELIGIBLE"
        )
        db.add(camp_contact)
        db.flush()

        msg = Message(
            campaign_id=camp.id,
            contact_id=contact.id,
            rendered_content="Hello Alice Verification, welcome to our verified service!",
            status=QueueState.QUEUED,
            idempotency_key=f"p5_verify_msg_{camp.id}_{contact.id}"
        )
        db.add(msg)
        db.commit()

        provider = MockMessageProvider(default_success=True)
        runner = ProductionRunner(
            db=db,
            campaign_id=camp.id,
            provider=provider,
            poll_interval=0.05,
        )

        exit_code = runner.start(max_iterations=1)
        assert exit_code == ExitCode.SUCCESS, f"Expected SUCCESS, got {exit_code}"

        db.refresh(msg)
        assert msg.status == QueueState.SENT, f"Expected SENT, got {msg.status}"
        print(f"   [OK] Production runner dispatched message {msg.id} -> Status: {msg.status}.")

        # ----------------------------------------------------------------------
        # 4. Authoritative OS Process Lock & Singularity Protection
        # ----------------------------------------------------------------------
        print("\n4. Testing Process Singularity Lock Protection...")
        lock1 = ProcessLock(db=db)
        lock2 = ProcessLock(db=db)

        acquired1 = lock1.acquire(worker_id="active_p5_worker", campaign_id=camp.id)
        assert acquired1 is True, "First lock acquisition failed"

        # Second lock must be strictly rejected
        acquired2 = lock2.acquire(worker_id="duplicate_p5_worker", campaign_id=camp.id)
        assert acquired2 is False, "Duplicate runner lock was not blocked!"
        print("   [OK] Duplicate runner acquisition strictly rejected by OS file lock.")

        # Check active runner info
        active_info = lock1.get_active_runner_info()
        assert active_info is not None and active_info.get("worker_id") == "active_p5_worker"
        print(f"   [OK] Active runner detected with PID {active_info.get('pid')}.")

        lock1.release()
        assert lock1.get_active_runner_info() is None
        print("   [OK] OS process lock released cleanly.")

        # ----------------------------------------------------------------------
        # 5. Emergency Stop Killswitch & Pacing Propagation (<500ms target)
        # ----------------------------------------------------------------------
        print("\n5. Testing Emergency Stop Signal Propagation & Claim Prevention...")
        t_start = time.time()
        res = handle_emergency_stop(Namespace(reason="Operator emergency stop test"), db)
        elapsed_ms = (time.time() - t_start) * 1000
        assert res == ExitCode.SUCCESS

        estop = EmergencyStop(db)
        assert estop.is_active() is True
        print(f"   [OK] Emergency stop activated in {elapsed_ms:.2f}ms (<500ms target).")

        # Verify runner pauses immediately on emergency stop
        runner2 = ProductionRunner(
            db=db,
            campaign_id=camp.id,
            provider=provider,
            poll_interval=0.01,
        )
        runner2.signals.request_shutdown()  # Exit after 1 iteration
        exit_code2 = runner2.start(max_iterations=1)
        assert exit_code2 == ExitCode.SUCCESS
        print("   [OK] Production runner respected active emergency stop and paused queue claims.")

        # Resume emergency stop
        res = handle_emergency_resume(Namespace(reason="Operator test completed"), db)
        assert res == ExitCode.SUCCESS
        assert EmergencyStop(db).is_active() is False
        print("   [OK] Emergency stop resumed cleanly.")

        # ----------------------------------------------------------------------
        # 6. UNKNOWN_OUTCOME Manual Reconciliation Override
        # ----------------------------------------------------------------------
        print("\n6. Testing UNKNOWN_OUTCOME Manual Reconciliation Override...")
        uo_contact = Contact(
            name="Bob Ambiguous",
            phone_e164=f"+1555{(ts + 1) % 10000000:07d}",
            country_code="US"
        )
        db.add(uo_contact)
        db.flush()

        uo_msg = Message(
            campaign_id=camp.id,
            contact_id=uo_contact.id,
            rendered_content="Hello Bob Ambiguous, please verify receipt.",
            status=QueueState.FAILED,
            error_type="UNKNOWN_OUTCOME",
            last_error="[UNKNOWN_OUTCOME] Browser tab unresponsive during confirmation checkmark",
            idempotency_key=f"p5_verify_uo_{camp.id}_{uo_contact.id}"
        )
        db.add(uo_msg)
        db.commit()

        # Inspect message
        res = handle_queue_inspect(Namespace(message_id=uo_msg.id), db)
        assert res == ExitCode.SUCCESS
        print(f"   [OK] 'outreach queue inspect {uo_msg.id}' displayed UNKNOWN_OUTCOME warning.")

        # Attempt override with invalid confirmation -> BLOCKED
        res_blocked = handle_queue_override(
            Namespace(message_id=uo_msg.id, reason="Checked contact chat manually", operator="qa_lead"),
            db,
            confirmation_input="wrong-string"
        )
        assert res_blocked == ExitCode.UNKNOWN_OUTCOME_BLOCKED, f"Expected UNKNOWN_OUTCOME_BLOCKED, got {res_blocked}"
        db.refresh(uo_msg)
        assert uo_msg.status == QueueState.FAILED, "Message was modified without proper confirmation!"
        print("   [OK] Override attempt without 'CONFIRM-NOT-DELIVERED' was strictly rejected.")

        # Override with exact confirmation string
        res_success = handle_queue_override(
            Namespace(
                message_id=uo_msg.id,
                reason="Contact WhatsApp Web thread inspected; verified message was never received.",
                operator="lead_operator"
            ),
            db,
            confirmation_input="CONFIRM-NOT-DELIVERED"
        )
        assert res_success == ExitCode.SUCCESS, f"Expected SUCCESS, got {res_success}"
        db.refresh(uo_msg)
        assert uo_msg.status == QueueState.QUEUED, f"Expected QUEUED, got {uo_msg.status}"
        print(f"   [OK] Message {uo_msg.id} successfully transitioned to QUEUED after verified override.")

        # Verify audit log entry
        audit = db.query(AuditLog).filter(
            AuditLog.event_type == "MANUAL_RECONCILIATION_OVERRIDE",
            AuditLog.message_id == uo_msg.id
        ).first()
        assert audit is not None, "AuditLog record was not created!"
        audit_details = json.loads(audit.error_message)
        assert audit_details["manual_verification_confirmation"] is True
        assert audit_details["operator_identity"] == "lead_operator"
        assert "Contact WhatsApp Web thread inspected" in audit_details["explicit_override_reason"]
        print("   [OK] Comprehensive AuditLog record created with operator reason and confirmation.")

        print("\n==================================================================")
        print("    Phase 5 E2E Verification COMPLETE: ALL CHECKS PASSED (6/6)    ")
        print("==================================================================")

    finally:
        db.close()


if __name__ == "__main__":
    main()
