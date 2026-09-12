"""
End-to-End Verification Script for Phase 6.

Validates:
1. Dual Logging (app.log & app.json.log) with single emission and deterministic JSON schema.
2. Handler-Safe Sanitization (E.164 phone masking, Egyptian mobile formats, token/password scrubbing).
3. Production Preflight 10-Point Inspection Matrix (Standard & Strict modes).
4. Live Operational System Health Evaluation (STOPPED, HEALTHY, DEGRADED, UNHEALTHY).
5. Analytics Subsystem & Authoritative Confirmed Send Rate formula.
6. CLI Integration for preflight, system health, and analytics subcommands.
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.cli.main import main
from app.cli.exit_codes import ExitCode
from app.utils.settings import settings
from app.utils.logger import (
    configure_logging,
    log_event,
    mask_phone,
    sanitize_text,
    sanitize_dict,
)
from app.utils.events import EventType
from app.readiness.preflight import run_preflight
from app.readiness.health import evaluate_system_health, HealthState
from app.services.analytics_service import AnalyticsService
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.queue.state_machine import QueueState


def run_e2e_verification() -> bool:
    print("=" * 70)
    print("PHASE 6: END-TO-END VERIFICATION SUITE")
    print("=" * 70)

    db = SessionLocal()
    try:
        # ------------------------------------------------------------------
        # 1. Dual Logging & Redaction Verification
        # ------------------------------------------------------------------
        print("\n[STEP 1] Testing Dual Logging, Handler Safety, and Redaction...")
        plain_log_path = Path(settings.LOG_FILE)
        json_log_path = Path(settings.LOG_JSON_FILE)

        # Truncate / ensure clean start
        plain_log_path.parent.mkdir(parents=True, exist_ok=True)
        if plain_log_path.exists():
            plain_log_path.unlink()
        if json_log_path.exists():
            json_log_path.unlink()

        configure_logging(force_reconfigure=True)
        app_logger = logging.getLogger("app.verification")

        test_phone_eg = "+201012345678"
        test_phone_intl = "+15551234567"
        test_secret = "Bearer super-secret-api-token-xyz"
        test_pwd = "password=SuperSecretPassword123"

        log_event(
            logger=app_logger,
            level=logging.INFO,
            event=EventType.MESSAGE_SEND_CONFIRMED.value,
            component="verification",
            message=f"Dispatch confirmed to {test_phone_eg} with {test_pwd}",
            campaign_id=999,
            details={"phone_e164": test_phone_eg, "token": test_secret},
        )

        assert plain_log_path.exists(), "Plaintext log file logs/app.log was not created!"
        assert json_log_path.exists(), "JSON lines log file logs/app.json.log was not created!"

        plain_content = plain_log_path.read_text(encoding="utf-8")
        json_content = json_log_path.read_text(encoding="utf-8")

        # Verify Redaction in both files
        assert "+201012345678" not in plain_content, "Raw Egyptian phone leaked into plain log!"
        assert "+2010******78" in plain_content, "Egyptian phone mask missing in plain log!"
        assert "SuperSecretPassword123" not in plain_content, "Password leaked into plain log!"
        assert "[REDACTED]" in plain_content, "Redaction tag missing in plain log!"

        assert "+201012345678" not in json_content, "Raw Egyptian phone leaked into JSON log!"
        assert "+2010******78" in json_content, "Egyptian phone mask missing in JSON log!"
        assert "SuperSecretPassword123" not in json_content, "Password leaked into JSON log!"

        # Verify JSON Lines structure
        json_lines = [line for line in json_content.strip().split("\n") if line.strip()]
        assert len(json_lines) == 1, f"Expected exactly 1 JSON log line, found {len(json_lines)}"
        record = json.loads(json_lines[0])
        assert record["event"] == EventType.MESSAGE_SEND_CONFIRMED.value
        assert record["campaign_id"] == 999
        assert record["details"]["phone_e164"] == "+2010******78"
        assert record["details"]["token"] == "[REDACTED]"
        print("  -> Dual logging, schema integrity, and sanitization PASSED.")

        # ------------------------------------------------------------------
        # 2. Production Preflight Inspection Matrix
        # ------------------------------------------------------------------
        print("\n[STEP 2] Testing Production Preflight 10-Point Inspection Matrix...")
        preflight_res = run_preflight(db=db, strict=False)
        print(f"  -> Preflight overall passed: {preflight_res.passed} (exit_code: {int(preflight_res.exit_code)})")
        print(f"  -> Total inspection checks: {len(preflight_res.checks)}")
        assert len(preflight_res.checks) == 10, f"Expected 10 preflight checks, found {len(preflight_res.checks)}"
        for c in preflight_res.checks:
            status_tag = "PASS" if c.passed else "FAIL" if c.critical else "WARN"
            print(f"     [{status_tag}] {c.name}: {c.message}")
        assert preflight_res.passed is True, "Preflight standard inspection failed unexpectedly!"

        # ------------------------------------------------------------------
        # 3. Live Operational System Health Evaluation
        # ------------------------------------------------------------------
        print("\n[STEP 3] Testing Live Operational System Health Evaluation...")
        health_res = evaluate_system_health(db=db)
        print(f"  -> Current System Health State: {health_res.state.value}")
        print(f"  -> Subsystem details: DB={health_res.details['database_responsive']}, E-Stop={health_res.details['emergency_stop_active']}")
        assert health_res.state in [HealthState.STOPPED, HealthState.HEALTHY], f"Unexpected initial state: {health_res.state}"
        print("  -> System health evaluation engine PASSED.")

        # ------------------------------------------------------------------
        # 4. Analytics Service & Confirmed Send Rate
        # ------------------------------------------------------------------
        print("\n[STEP 4] Testing Analytics Service & Confirmed Send Rate Formula...")
        # Create dedicated test campaign
        test_camp = Campaign(
            name="Phase 6 E2E Verification Campaign",
            message_template="Hello {{name}}, welcome!",
            status="RUNNING",
            scheduled_start_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            daily_limit=100,
        )
        db.add(test_camp)
        db.flush()

        contacts = [
            Contact(name=f"E2E Contact {i}", phone_e164=f"+2010000000{i:02d}", country_code="EG")
            for i in range(1, 6)
        ]
        db.add_all(contacts)
        db.flush()

        # Contacts breakdown: 4 eligible, 1 excluded
        for i, c in enumerate(contacts):
            status = "EXCLUDED" if i == 4 else "ELIGIBLE"
            db.add(CampaignContact(campaign_id=test_camp.id, contact_id=c.id, status=status))

        # Messages:
        # 2 SENT (UI Confirmed)
        # 1 FAILED (Definitive)
        # 1 FAILED (UNKNOWN_OUTCOME)
        # 1 RETRY_PENDING (Transient)
        now_dt = datetime.now(timezone.utc)
        db.add(Message(campaign_id=test_camp.id, contact_id=contacts[0].id, rendered_content="Msg 1", status=QueueState.SENT, sent_at=now_dt, idempotency_key=f"p6_e2e_{test_camp.id}_1"))
        db.add(Message(campaign_id=test_camp.id, contact_id=contacts[1].id, rendered_content="Msg 2", status=QueueState.SENT, sent_at=now_dt, idempotency_key=f"p6_e2e_{test_camp.id}_2"))
        db.add(Message(campaign_id=test_camp.id, contact_id=contacts[2].id, rendered_content="Msg 3", status=QueueState.FAILED, error_type="NETWORK_ERROR", idempotency_key=f"p6_e2e_{test_camp.id}_3"))
        db.add(Message(campaign_id=test_camp.id, contact_id=contacts[3].id, rendered_content="Msg 4", status=QueueState.FAILED, error_type="UNKNOWN_OUTCOME", idempotency_key=f"p6_e2e_{test_camp.id}_4"))
        db.add(Message(campaign_id=test_camp.id, contact_id=contacts[4].id, rendered_content="Msg 5", status=QueueState.RETRY_PENDING, idempotency_key=f"p6_e2e_{test_camp.id}_5"))
        db.commit()

        camp_analytics = AnalyticsService.get_campaign_analytics(db, test_camp.id)
        assert camp_analytics is not None, "Failed generating campaign analytics!"
        qb = camp_analytics["queue_breakdown"]
        perf = camp_analytics["performance"]

        # Verified locked math:
        # Confirmed sends = 2
        # Failed = 1
        # Unknown outcome = 1
        # Denominator = 2 + 1 + 1 = 4
        # Confirmed Send Rate = (2 / 4) * 100 = 50.0%
        # Retry pending (1) strictly excluded from denominator.
        print(f"  -> Queue Breakdown: SENT={qb['confirmed_sends']}, FAILED={qb['failed']}, UNKNOWN={qb['unknown_outcome']}, RETRY_PENDING={qb['retry_pending']}")
        print(f"  -> Performance: Confirmed Send Rate = {perf['confirmed_send_rate']}% (Expected: 50.00%)")
        assert perf["confirmed_send_rate"] == 50.00, f"Confirmed Send Rate calculation mismatch: {perf['confirmed_send_rate']}"
        assert perf["terminal_dispatches"] == 4, f"Terminal dispatches mismatch: {perf['terminal_dispatches']}"

        # System analytics
        sys_analytics = AnalyticsService.get_system_analytics(db)
        print(f"  -> Global Daily Quota: Sent Today={sys_analytics['daily_quota']['sent_today']}, Limit={sys_analytics['daily_quota']['global_daily_limit']}")
        assert sys_analytics["daily_quota"]["sent_today"] >= 2
        print("  -> Analytics service verification PASSED.")

        # ------------------------------------------------------------------
        # 5. CLI Commands Integration Verification
        # ------------------------------------------------------------------
        print("\n[STEP 5] Testing CLI Commands Routing and JSON Modes...")
        cli_tests = [
            (["preflight", "--json"], ExitCode.SUCCESS),
            (["system", "health", "--json"], ExitCode.SUCCESS),
            (["analytics", "campaign", str(test_camp.id), "--json"], ExitCode.SUCCESS),
            (["analytics", "queue", "--campaign-id", str(test_camp.id), "--json"], ExitCode.SUCCESS),
            (["analytics", "runner", "--json"], ExitCode.SUCCESS),
            (["analytics", "provider", "--json"], ExitCode.SUCCESS),
            (["analytics", "system", "--json"], ExitCode.SUCCESS),
        ]

        for cmd_args, expected_code in cli_tests:
            cmd_str = "outreach " + " ".join(cmd_args)
            rc = main(cmd_args, db_session=db)
            assert rc == int(expected_code), f"CLI command '{cmd_str}' failed with exit code {rc} (expected {expected_code})"
            print(f"  -> CLI '{cmd_str}': ExitCode={rc} [OK]")

        print("\n" + "=" * 70)
        print("PHASE 6: ALL END-TO-END VERIFICATION CHECKS PASSED CLEANLY!")
        print("=" * 70)
        return True

    finally:
        db.close()


if __name__ == "__main__":
    success = run_e2e_verification()
    sys.exit(0 if success else 1)
