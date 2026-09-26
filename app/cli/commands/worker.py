"""
Production Worker Daemon CLI Commands.

Operational commands to start, inspect, and stop the always-on
Oracle worker control plane daemon.
"""

import os
import time
import signal
import logging
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import (
    print_header,
    print_card,
    print_success,
    print_error,
    print_info,
    print_warning_box,
)
from app.runner.worker_daemon import WorkerDaemon
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.models.app_setting import AppSetting
from app.runner.whatsapp_command_handler import validate_worker_instance_id
from app.utils.settings import settings

logger = logging.getLogger(__name__)


def handle_worker_start(args, db: Session) -> ExitCode:
    """
    Executes 'outreach worker start'.
    Starts the always-on worker control plane daemon loop.
    """
    print_header("Starting Worker Daemon", "Always-On Control Plane Service")

    # Validate WORKER_INSTANCE_ID
    raw_id = getattr(settings, "WORKER_INSTANCE_ID", "")
    if not raw_id or not validate_worker_instance_id(raw_id.strip()):
        print_error(
            f"WORKER_INSTANCE_ID '{raw_id}' is invalid or unconfigured.\n"
            "Worker instance ID must be 3-64 characters matching [a-z0-9-]."
        )
        return ExitCode.INVALID_ARGUMENT

    instance_id = raw_id.strip()
    poll_interval = getattr(args, "poll_interval", None)
    heartbeat_interval = getattr(args, "heartbeat_interval", None)
    max_iter = getattr(args, "max_iterations", None)

    daemon = WorkerDaemon(
        db=db,
        worker_instance_id=instance_id,
        poll_interval=poll_interval,
        heartbeat_interval=heartbeat_interval,
    )

    exit_code = daemon.start(max_iterations=max_iter)

    if exit_code == ExitCode.SUCCESS:
        print_success("Worker daemon stopped gracefully.")
    elif exit_code == ExitCode.CONCURRENCY_ERROR:
        print_error("Cannot start worker daemon: another worker daemon process is already active.")
        print_info("Run 'outreach worker status' to inspect.")
    else:
        print_error(f"Worker daemon exited with code {int(exit_code)}.")

    return exit_code


def handle_worker_status(args, db: Session) -> ExitCode:
    """
    Executes 'outreach worker status'.
    Inspects worker process singularity lock, identity, and infrastructure heartbeat.
    """
    import json
    print_header("Worker Daemon Status")

    lock_mgr = ProcessLock(db=None, lock_file_path=settings.WORKER_LOCK_FILE)
    active_info = lock_mgr.get_active_process_info()

    # Query worker identity and heartbeat from AppSetting
    identity_row = db.query(AppSetting).filter(AppSetting.key == "system:worker_identity").first()
    identity_data = json.loads(identity_row.value) if identity_row and identity_row.value else {}

    heartbeat_row = db.query(AppSetting).filter(AppSetting.key == "system:worker_heartbeat").first()
    heartbeat_data = json.loads(heartbeat_row.value) if heartbeat_row and heartbeat_row.value else {}

    if active_info:
        card_data = {
            "State": "ACTIVE",
            "PID": active_info.get("pid"),
            "Worker Instance ID": active_info.get("worker_id") or identity_data.get("instance_id", "Unknown"),
            "Started At": active_info.get("started_at"),
            "Last Heartbeat": heartbeat_data.get("last_seen", active_info.get("last_heartbeat")),
            "Runner State": heartbeat_data.get("runner_state", "STANDBY"),
            "Xvfb Display": identity_data.get("xvfb_display", os.environ.get("DISPLAY", ":99")),
            "Chrome Available": "YES" if heartbeat_data.get("chrome_reachable") else "NO",
            "Lock File": settings.WORKER_LOCK_FILE,
        }
        print_card("Active Worker Daemon", card_data)
        print_info("Worker daemon is active and maintaining control-plane connectivity.")
    else:
        print_card("Worker Daemon", {
            "State": "STOPPED",
            "Active PID": "None",
            "Worker Instance ID": identity_data.get("instance_id", getattr(settings, "WORKER_INSTANCE_ID", "Unconfigured")),
            "Last Known Heartbeat": heartbeat_data.get("last_seen", "None"),
            "Lock File": settings.WORKER_LOCK_FILE,
        })
        print_info("No worker daemon process is currently active on this system.")

    return ExitCode.SUCCESS


def handle_worker_stop(args, db: Session) -> ExitCode:
    """
    Executes 'outreach worker stop'.
    Sends graceful termination signal (SIGTERM) to the running worker daemon PID.
    Does NOT force-kill child ProductionRunner or Chrome processes.
    """
    print_header("Stopping Worker Daemon")

    lock_mgr = ProcessLock(db=None, lock_file_path=settings.WORKER_LOCK_FILE)
    active_info = lock_mgr.get_active_process_info()

    if not active_info or not active_info.get("pid"):
        print_info("No active worker daemon process was detected to stop.")
        return ExitCode.SUCCESS

    pid = active_info["pid"]
    print(f"Signaling active worker daemon (PID: {pid})...")

    try:
        os.kill(pid, signal.SIGTERM)

        # Await graceful shutdown
        wait_seconds = 15
        end_time = time.time() + wait_seconds
        while time.time() < end_time:
            if not is_pid_alive(pid):
                break
            time.sleep(0.5)

        if not is_pid_alive(pid):
            print_success(f"Worker daemon (PID {pid}) stopped gracefully.")
            lock_mgr.release()
            return ExitCode.SUCCESS
        else:
            print_warning_box(
                f"Worker daemon (PID {pid}) did not exit within {wait_seconds}s.\n"
                "It may be awaiting completion of an in-flight command.",
                title="WORKER STOP IN PROGRESS"
            )
            return ExitCode.SUCCESS

    except ProcessLookupError:
        print_info(f"Process {pid} already terminated.")
        lock_mgr.release()
        return ExitCode.SUCCESS
    except Exception as e:
        print_error(f"Could not signal worker PID {pid}: {e}")
        return ExitCode.GENERAL_ERROR
