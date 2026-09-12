"""
Campaign Operations CLI Commands.

Handles campaign business state transitions (run, status, pause, resume, stop).
Execution is strictly decoupled from state transitions; commands here do NOT
spawn or execute background runners.
"""

import logging
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.cli.exit_codes import ExitCode
from app.cli.output import print_header, print_card, print_success, print_error, print_info
from app.models.campaign import Campaign
from app.models.message import Message
from app.campaigns.manager import CampaignManager
from app.campaigns.exceptions import CampaignNotFoundError, InvalidStateTransitionError

logger = logging.getLogger(__name__)


def handle_campaign_run(args, db: Session) -> ExitCode:
    """
    Executes 'outreach campaign run <campaign_id>'.
    Performs business state transition to RUNNING. Does NOT launch runner daemon.
    """
    campaign_id = args.campaign_id
    print_header("Campaign Run (State Transition)", f"Campaign ID: {campaign_id}")

    manager = CampaignManager(db)
    try:
        campaign = manager.get_campaign(campaign_id)
    except CampaignNotFoundError:
        print_error(f"Campaign {campaign_id} does not exist.")
        return ExitCode.NOT_FOUND

    if campaign.status == "RUNNING":
        print_info(f"Campaign '{campaign.name}' (ID: {campaign_id}) is already in RUNNING state.")
        print_info(f"To start worker processing, run: outreach runner start --campaign-id {campaign_id}")
        return ExitCode.SUCCESS

    try:
        campaign = manager.transition_state(campaign_id, "RUNNING")
        print_success(f"Campaign '{campaign.name}' (ID: {campaign_id}) transitioned to RUNNING.")
        print_card("Campaign Status", {
            "ID": campaign.id,
            "Name": campaign.name,
            "Status": campaign.status,
            "Daily Limit": campaign.daily_limit,
            "Pacing (min/max)": f"{campaign.min_delay_seconds}s - {campaign.max_delay_seconds}s",
        })
        print_info(f"Ready for dispatch. To start worker execution, run:")
        print_info(f"  outreach runner start --campaign-id {campaign_id}")
        return ExitCode.SUCCESS

    except InvalidStateTransitionError as e:
        print_error(f"Invalid state transition: {e}")
        return ExitCode.INVALID_STATE
    except Exception as e:
        print_error(f"Failed to transition campaign: {e}")
        return ExitCode.GENERAL_ERROR


def handle_campaign_status(args, db: Session) -> ExitCode:
    """
    Executes 'outreach campaign status <campaign_id>'.
    Displays campaign configuration, pacing, and detailed queue breakdown.
    """
    campaign_id = args.campaign_id
    print_header("Campaign Status", f"Campaign ID: {campaign_id}")

    manager = CampaignManager(db)
    try:
        campaign = manager.get_campaign(campaign_id)
    except CampaignNotFoundError:
        print_error(f"Campaign {campaign_id} does not exist.")
        return ExitCode.NOT_FOUND

    # Query queue counts
    status_counts = dict(
        db.query(Message.status, func.count(Message.id))
        .filter(Message.campaign_id == campaign_id)
        .group_by(Message.status)
        .all()
    )

    # Count UNKNOWN_OUTCOME specifically
    unknown_outcome_count = (
        db.query(func.count(Message.id))
        .filter(
            Message.campaign_id == campaign_id,
            Message.status == "FAILED",
            Message.error_type == "UNKNOWN_OUTCOME"
        )
        .scalar()
        or 0
    )

    print_card("Campaign Configuration", {
        "ID": campaign.id,
        "Name": campaign.name,
        "Status": campaign.status,
        "Daily Limit": campaign.daily_limit,
        "Pacing Delays": f"{campaign.min_delay_seconds}s - {campaign.max_delay_seconds}s",
        "Batch Size": campaign.batch_size,
        "Batch Pause": f"{campaign.batch_pause_seconds}s",
        "Error Threshold": campaign.error_threshold,
    })

    print_card("Message Queue Breakdown", {
        "PENDING": status_counts.get("PENDING", 0),
        "QUEUED": status_counts.get("QUEUED", 0),
        "PROCESSING": status_counts.get("PROCESSING", 0),
        "SENT": status_counts.get("SENT", 0),
        "RETRY_PENDING": status_counts.get("RETRY_PENDING", 0),
        "FAILED": status_counts.get("FAILED", 0),
        "UNKNOWN_OUTCOME": unknown_outcome_count,
        "SKIPPED": status_counts.get("SKIPPED", 0),
        "CANCELLED": status_counts.get("CANCELLED", 0),
    })

    return ExitCode.SUCCESS


def handle_campaign_pause(args, db: Session) -> ExitCode:
    """
    Executes 'outreach campaign pause <campaign_id>'.
    Transitions RUNNING -> PAUSED.
    """
    campaign_id = args.campaign_id
    print_header("Pause Campaign", f"Campaign ID: {campaign_id}")

    manager = CampaignManager(db)
    try:
        campaign = manager.get_campaign(campaign_id)
    except CampaignNotFoundError:
        print_error(f"Campaign {campaign_id} does not exist.")
        return ExitCode.NOT_FOUND

    if campaign.status == "PAUSED":
        print_info(f"Campaign '{campaign.name}' (ID: {campaign_id}) is already PAUSED.")
        return ExitCode.SUCCESS

    try:
        campaign = manager.transition_state(campaign_id, "PAUSED")
        print_success(f"Campaign '{campaign.name}' (ID: {campaign_id}) is now PAUSED.")
        print_info("Any active production runner will pause claiming messages at its next cancellation point.")
        return ExitCode.SUCCESS
    except InvalidStateTransitionError as e:
        print_error(f"Invalid state transition: {e}")
        return ExitCode.INVALID_STATE


def handle_campaign_resume(args, db: Session) -> ExitCode:
    """
    Executes 'outreach campaign resume <campaign_id>'.
    Transitions PAUSED -> RUNNING.
    """
    campaign_id = args.campaign_id
    print_header("Resume Campaign", f"Campaign ID: {campaign_id}")

    manager = CampaignManager(db)
    try:
        campaign = manager.get_campaign(campaign_id)
    except CampaignNotFoundError:
        print_error(f"Campaign {campaign_id} does not exist.")
        return ExitCode.NOT_FOUND

    if campaign.status == "RUNNING":
        print_info(f"Campaign '{campaign.name}' (ID: {campaign_id}) is already RUNNING.")
        return ExitCode.SUCCESS

    try:
        campaign = manager.transition_state(campaign_id, "RUNNING")
        print_success(f"Campaign '{campaign.name}' (ID: {campaign_id}) is now resumed (RUNNING).")
        print_info(f"Active or future runner will continue message dispatches.")
        return ExitCode.SUCCESS
    except InvalidStateTransitionError as e:
        print_error(f"Invalid state transition: {e}")
        return ExitCode.INVALID_STATE


def handle_campaign_stop(args, db: Session) -> ExitCode:
    """
    Executes 'outreach campaign stop <campaign_id>'.
    Transitions RUNNING/PAUSED -> CANCELLED.
    """
    campaign_id = args.campaign_id
    print_header("Stop Campaign", f"Campaign ID: {campaign_id}")

    manager = CampaignManager(db)
    try:
        campaign = manager.get_campaign(campaign_id)
    except CampaignNotFoundError:
        print_error(f"Campaign {campaign_id} does not exist.")
        return ExitCode.NOT_FOUND

    if campaign.status == "CANCELLED":
        print_info(f"Campaign '{campaign.name}' (ID: {campaign_id}) is already CANCELLED.")
        return ExitCode.SUCCESS

    try:
        campaign = manager.transition_state(campaign_id, "CANCELLED")
        # Cancel remaining pending or queued messages
        cancelled_rows = (
            db.query(Message)
            .filter(
                Message.campaign_id == campaign_id,
                Message.status.in_(["PENDING", "QUEUED", "RETRY_PENDING"])
            )
            .update({"status": "CANCELLED"}, synchronize_session=False)
        )
        db.commit()

        print_success(f"Campaign '{campaign.name}' (ID: {campaign_id}) is now CANCELLED.")
        print_info(f"Cancelled {cancelled_rows} remaining queued/pending messages.")
        return ExitCode.SUCCESS
    except InvalidStateTransitionError as e:
        print_error(f"Invalid state transition: {e}")
        return ExitCode.INVALID_STATE
