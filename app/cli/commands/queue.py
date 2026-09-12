"""
Queue Operations and UNKNOWN_OUTCOME Manual Override CLI Commands.

Handles queue inspection, status reporting, stale lease reconciliation,
and strictly audited manual override for UNKNOWN_OUTCOME messages.
"""

import json
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.cli.exit_codes import ExitCode
from app.cli.output import (
    print_header, print_card, print_table, print_warning_box,
    print_success, print_error, print_info, mask_phone
)
from app.models.message import Message
from app.models.contact import Contact
from app.models.audit_log import AuditLog
from app.queue.service import PersistentQueueService

logger = logging.getLogger(__name__)


def handle_queue_status(args, db: Session) -> ExitCode:
    """
    Executes 'outreach queue status [--campaign-id <id>]'.
    Displays message counts across all statuses with dedicated UNKNOWN_OUTCOME highlight.
    """
    campaign_id = getattr(args, "campaign_id", None)
    subtitle = f"Campaign ID: {campaign_id}" if campaign_id else "All Campaigns"
    print_header("Message Queue Status", subtitle)

    query = db.query(Message.status, func.count(Message.id))
    if campaign_id:
        query = query.filter(Message.campaign_id == campaign_id)
    status_counts = dict(query.group_by(Message.status).all())

    uo_query = db.query(func.count(Message.id)).filter(
        Message.status == "FAILED",
        Message.error_type == "UNKNOWN_OUTCOME"
    )
    if campaign_id:
        uo_query = uo_query.filter(Message.campaign_id == campaign_id)
    unknown_outcome_count = uo_query.scalar() or 0

    headers = ["Status", "Count", "Description"]
    rows = [
        ["PENDING", status_counts.get("PENDING", 0), "Waiting on schedule or batch start"],
        ["QUEUED", status_counts.get("QUEUED", 0), "Ready for immediate worker claiming"],
        ["PROCESSING", status_counts.get("PROCESSING", 0), "Active lease held by worker"],
        ["SENT", status_counts.get("SENT", 0), "Successfully sent and confirmed (SEND_CONFIRMED)"],
        ["RETRY_PENDING", status_counts.get("RETRY_PENDING", 0), "Temporary failure; scheduled for backoff retry"],
        ["FAILED", status_counts.get("FAILED", 0), "Permanent failure or attempts exhausted"],
        ["UNKNOWN_OUTCOME", unknown_outcome_count, "Interrupted post-send; automatic retry BLOCKED"],
        ["SKIPPED", status_counts.get("SKIPPED", 0), "Excluded by frequency or eligibility rules"],
        ["CANCELLED", status_counts.get("CANCELLED", 0), "Aborted or cancelled by operator"],
    ]
    print_table(headers, rows)

    if unknown_outcome_count > 0:
        print_warning_box(
            f"There are {unknown_outcome_count} message(s) in UNKNOWN_OUTCOME status.\n"
            "External send result is unknown. Automatic blind retry is prohibited.\n"
            "Run 'outreach queue reconcile' to inspect and reconcile.",
            title="UNKNOWN_OUTCOME MESSAGES PENDING"
        )

    return ExitCode.SUCCESS


def handle_queue_inspect(args, db: Session) -> ExitCode:
    """
    Executes 'outreach queue inspect <message_id>'.
    Displays full details for a message record, masking sensitive contact data.
    """
    message_id = args.message_id
    print_header("Message Queue Inspection", f"Message ID: {message_id}")

    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        print_error(f"Message {message_id} not found.")
        return ExitCode.NOT_FOUND

    contact = db.query(Contact).filter(Contact.id == message.contact_id).first()
    masked_phone_num = mask_phone(contact.phone_e164 if contact else None)

    card_data = {
        "Message ID": message.id,
        "Campaign ID": message.campaign_id,
        "Contact ID": message.contact_id,
        "Recipient Phone": masked_phone_num,
        "Status": message.status,
        "Error Type": message.error_type or "None",
        "Attempts": f"{message.attempt_count} / {message.max_attempts}",
        "Idempotency Key": message.idempotency_key,
        "Locked By": message.locked_by or "None",
        "Locked At": str(message.locked_at) if message.locked_at else "None",
        "Last Attempt At": str(message.last_attempt_at) if message.last_attempt_at else "None",
        "Next Retry At": str(message.next_retry_at) if message.next_retry_at else "None",
        "Last Error": message.last_error or "None",
    }
    print_card("Message Record", card_data)

    if message.error_type == "UNKNOWN_OUTCOME":
        print_warning_box(
            "CRITICAL WARNING: UNKNOWN_OUTCOME\n"
            "An external send action occurred but confirmation was not observed.\n"
            "Retrying this message MAY RESULT IN DUPLICATE EXTERNAL DELIVERY.\n"
            "You MUST manually inspect WhatsApp Web before overriding.\n"
            f"To override: outreach queue override {message_id} --reason \"<verified reason>\"",
            title="UNKNOWN_OUTCOME ALERT"
        )

    return ExitCode.SUCCESS


def handle_queue_reconcile(args, db: Session) -> ExitCode:
    """
    Executes 'outreach queue reconcile [--campaign-id <id>]'.
    Recovers stale leases and reports un-reconciled UNKNOWN_OUTCOME messages.
    """
    campaign_id = getattr(args, "campaign_id", None)
    print_header("Queue Reconciliation", f"Campaign: {campaign_id or 'All'}")

    queue_service = PersistentQueueService(db)
    recovered_count = queue_service.recover_stale_leases()
    print_success(f"Recovered {recovered_count} stale message lease(s).")

    # Inspect pending UNKNOWN_OUTCOME messages
    uo_query = db.query(Message).filter(
        Message.status == "FAILED",
        Message.error_type == "UNKNOWN_OUTCOME"
    )
    if campaign_id:
        uo_query = uo_query.filter(Message.campaign_id == campaign_id)
    unknown_messages = uo_query.limit(20).all()

    if unknown_messages:
        print_warning_box(
            f"Found {len(unknown_messages)} message(s) with UNKNOWN_OUTCOME.\n"
            "Automatic retry is disabled. Operator reconciliation is required.",
            title="ATTENTION REQUIRED"
        )
        headers = ["ID", "Campaign", "Contact ID", "Last Error"]
        rows = [[m.id, m.campaign_id, m.contact_id, (m.last_error or "")[:40]] for m in unknown_messages]
        print_table(headers, rows)
    else:
        print_info("No UNKNOWN_OUTCOME messages pending review.")

    return ExitCode.SUCCESS


def handle_queue_override(args, db: Session, confirmation_input: str = None) -> ExitCode:
    """
    Executes 'outreach queue override <message_id> --reason <text>'.
    Strict manual reconciliation for UNKNOWN_OUTCOME messages.
    Requires explicit reason and verification confirmation. No generic --force bypass.
    """
    message_id = args.message_id
    reason = getattr(args, "reason", None)
    print_header("Manual Override for UNKNOWN_OUTCOME", f"Message ID: {message_id}")

    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        print_error(f"Message {message_id} not found.")
        return ExitCode.NOT_FOUND

    if message.error_type != "UNKNOWN_OUTCOME":
        print_error(
            f"Message {message_id} has error_type '{message.error_type}'. "
            "Manual override is strictly reserved for UNKNOWN_OUTCOME messages."
        )
        return ExitCode.INVALID_STATE

    if not reason or not reason.strip():
        print_error("An explicit explanation reason is required via '--reason <explanation>'.")
        return ExitCode.INVALID_ARGUMENT

    print_warning_box(
        f"CRITICAL OPERATOR WARNING:\n"
        f"Message ID: {message_id} is in UNKNOWN_OUTCOME status.\n"
        "An external send action occurred, but confirmation was interrupted.\n"
        "Retrying this message MAY RESULT IN DUPLICATE EXTERNAL DELIVERY.\n\n"
        "You must have manually verified in WhatsApp Web that the recipient\n"
        "did NOT receive this message before confirming.",
        title="DUPLICATE DELIVERY RISK"
    )

    # Confirmation requirement
    required_confirmation = "CONFIRM-NOT-DELIVERED"
    if confirmation_input is None:
        try:
            user_input = input(f"Type '{required_confirmation}' to confirm override: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nOperation cancelled by user.")
            return ExitCode.UNKNOWN_OUTCOME_BLOCKED
    else:
        user_input = confirmation_input.strip()

    if user_input != required_confirmation:
        print_error(f"Confirmation string mismatch. Expected '{required_confirmation}', got '{user_input}'.")
        print_error("Manual override cancelled. Message remains FAILED with UNKNOWN_OUTCOME.")
        return ExitCode.UNKNOWN_OUTCOME_BLOCKED

    # Execute override: reset to QUEUED
    now = datetime.now(timezone.utc)
    previous_state = message.status
    message.status = "QUEUED"
    message.locked_at = None
    message.locked_by = None
    message.updated_at = now
    message.last_error = f"[MANUAL_OVERRIDE] {reason.strip()}"

    # Build comprehensive audit payload
    audit_payload = {
        "message_id": message.id,
        "previous_state": previous_state,
        "new_state": "QUEUED",
        "timestamp": now.isoformat(),
        "operator_identity": getattr(args, "operator", None) or "OPERATOR_CLI",
        "explicit_override_reason": reason.strip(),
        "manual_verification_confirmation": True,
        "override_action": "MANUAL_RECONCILIATION_OVERRIDE",
    }

    log = AuditLog(
        event_type="MANUAL_RECONCILIATION_OVERRIDE",
        campaign_id=message.campaign_id,
        message_id=message.id,
        status="QUEUED",
        result="Operator confirmed message was not delivered externally.",
        error_message=json.dumps(audit_payload)
    )
    db.add(log)
    db.commit()

    print_success(f"Message {message_id} successfully reconciled and transitioned to QUEUED.")
    print_info("A permanent audit record has been created for this reconciliation action.")
    return ExitCode.SUCCESS
