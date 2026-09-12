"""
Production Runner CLI Commands.

Operational commands to start, inspect, and stop the single-campaign
production runner daemon.
"""

import os
import time
import signal
import logging
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import print_header, print_card, print_success, print_error, print_info, print_warning_box
from app.runner.production_runner import ProductionRunner
from app.runner.process_lock import ProcessLock, is_pid_alive

logger = logging.getLogger(__name__)


def handle_runner_start(args, db: Session, provider=None) -> ExitCode:
    """
    Executes 'outreach runner start --campaign-id <id>'.
    Starts the long-running production runner daemon loop for a single campaign.
    """
    campaign_id = getattr(args, "campaign_id", None)
    if campaign_id is None:
        print_error("A target campaign ID is required. Use: outreach runner start --campaign-id <id>")
        return ExitCode.INVALID_ARGUMENT

    print_header("Starting Production Runner", f"Target Campaign ID: {campaign_id}")

    runner = ProductionRunner(
        db=db,
        campaign_id=campaign_id,
        provider=provider,
        poll_interval=getattr(args, "poll_interval", None),
    )

    max_iter = getattr(args, "max_iterations", None)
    exit_code = runner.start(max_iterations=max_iter)

    if exit_code == ExitCode.SUCCESS:
        print_success(f"Production runner completed gracefully for Campaign {campaign_id}.")
    elif exit_code == ExitCode.CONCURRENCY_ERROR:
        print_error("Cannot start runner: another production runner process is already active.")
        print_info("Only ONE production runner is permitted at a time. Run 'outreach runner status' to inspect.")
    elif exit_code == ExitCode.NOT_FOUND:
        print_error(f"Campaign {campaign_id} was not found.")
    elif exit_code == ExitCode.INVALID_STATE:
        print_error(f"Campaign {campaign_id} is not in RUNNING state. Run 'outreach campaign run {campaign_id}' first.")
    elif exit_code == ExitCode.AUTHENTICATION_REQUIRED:
        print_error("WhatsApp Web is not authenticated. Please run 'outreach session login' first.")
    elif exit_code == ExitCode.PROVIDER_UNAVAILABLE:
        print_error("WhatsApp provider is unavailable or could not connect.")
    else:
        print_error(f"Production runner exited with code {int(exit_code)}.")

    return exit_code


def handle_runner_status(args, db: Session) -> ExitCode:
    """
    Executes 'outreach runner status'.
    Inspects process singularity lock and database heartbeat for active runner.
    """
    print_header("Production Runner Status")

    lock_mgr = ProcessLock(db=db)
    active_info = lock_mgr.get_active_runner_info()

    if active_info:
        card_data = {
            "State": "RUNNING",
            "PID": active_info.get("pid"),
            "Worker ID": active_info.get("worker_id"),
            "Campaign ID": active_info.get("campaign_id"),
            "Started At": active_info.get("started_at"),
            "Last Heartbeat": active_info.get("last_heartbeat"),
            "Lock Authoritative": "YES (OS File Lock)",
        }
        print_card("Active Runner Process", card_data)
        print_info(f"Production runner is actively processing Campaign {active_info.get('campaign_id')}.")
    else:
        print_card("Active Runner Process", {
            "State": "STOPPED",
            "Active PID": "None",
            "Worker ID": "None",
            "Target Campaign": "None",
        })
        print_info("No production runner process is currently active on this system.")

    return ExitCode.SUCCESS


def handle_runner_stop(args, db: Session) -> ExitCode:
    """
    Executes 'outreach runner stop'.
    Sends termination signal to the running production runner PID.
    """
    print_header("Stopping Production Runner")

    lock_mgr = ProcessLock(db=db)
    active_info = lock_mgr.get_active_runner_info()

    if not active_info or not active_info.get("pid"):
        print_info("No active production runner process was detected to stop.")
        return ExitCode.SUCCESS

    pid = active_info["pid"]
    print(f"Signaling active production runner (PID: {pid})...")

    try:
        if os.name == "nt":
            # On Windows, terminate process
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)

        # Await graceful shutdown
        wait_seconds = 15
        end_time = time.time() + wait_seconds
        while time.time() < end_time:
            if not is_pid_alive(pid):
                break
            time.sleep(0.5)

        if not is_pid_alive(pid):
            print_success(f"Production runner (PID {pid}) stopped gracefully.")
            # Clean up stale locks if process exited
            lock_mgr.release()
            return ExitCode.SUCCESS
        else:
            print_warning_box(
                f"Runner (PID {pid}) did not exit within {wait_seconds}s.\n"
                "It may be awaiting completion of an in-flight send action.",
                title="RUNNER STOP IN PROGRESS"
            )
            return ExitCode.SUCCESS

    except ProcessLookupError:
        print_info(f"Process {pid} already terminated.")
        lock_mgr.release()
        return ExitCode.SUCCESS
    except Exception as e:
        print_error(f"Could not signal runner PID {pid}: {e}")
        return ExitCode.GENERAL_ERROR
