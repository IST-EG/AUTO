"""
Main CLI Entrypoint for Outreach Automation.

Usage:
  outreach session <login|status|logout>
  outreach campaign <run|status|pause|resume|stop>
  outreach runner <start|status|stop>
  outreach queue <status|inspect|reconcile|override>
  outreach emergency-stop
  outreach emergency-status
  outreach emergency-resume
"""

import sys
import argparse
import logging
from typing import Optional, List

from app.database import SessionLocal
from app.cli.exit_codes import ExitCode
from app.cli.output import print_error
from app.cli.commands.session import (
    handle_session_login,
    handle_session_status,
    handle_session_logout,
)
from app.cli.commands.campaign import (
    handle_campaign_run,
    handle_campaign_status,
    handle_campaign_pause,
    handle_campaign_resume,
    handle_campaign_stop,
)
from app.cli.commands.queue import (
    handle_queue_status,
    handle_queue_inspect,
    handle_queue_reconcile,
    handle_queue_override,
)
from app.cli.commands.emergency import (
    handle_emergency_stop,
    handle_emergency_status,
    handle_emergency_resume,
)
from app.cli.commands.runner import (
    handle_runner_start,
    handle_runner_status,
    handle_runner_stop,
)
from app.cli.commands.analytics import (
    handle_analytics_campaign,
    handle_analytics_queue,
    handle_analytics_runner,
    handle_analytics_provider,
    handle_analytics_system,
)
from app.cli.commands.preflight import handle_preflight
from app.cli.commands.health import handle_system_health
from app.cli.commands.auth import handle_auth_bootstrap_owner

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Builds the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="outreach",
        description="WhatsApp Outreach Automation Operational CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command category")

    # 1. Session Subcommands
    session_parser = subparsers.add_parser("session", help="WhatsApp Web session and browser management")
    session_sub = session_parser.add_subparsers(dest="subcommand", help="Session action")

    login_p = session_sub.add_parser("login", help="Launch browser and await QR authentication")
    login_p.add_argument("--timeout", type=int, default=None, help="QR wait timeout in seconds")
    login_p.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    session_sub.add_parser("status", help="Inspect session and profile health")

    logout_p = session_sub.add_parser("logout", help="Controlled local session shutdown")
    logout_p.add_argument("--clear-cache", action="store_true", help="Clear persistent browser cache directory")

    # 2. Campaign Subcommands (State transitions only)
    campaign_parser = subparsers.add_parser("campaign", help="Campaign business state operations")
    campaign_sub = campaign_parser.add_subparsers(dest="subcommand", help="Campaign action")

    run_p = campaign_sub.add_parser("run", help="Transition campaign state to RUNNING (business transition only)")
    run_p.add_argument("campaign_id", type=int, help="Target Campaign ID")

    status_p = campaign_sub.add_parser("status", help="Display campaign configuration and queue breakdown")
    status_p.add_argument("campaign_id", type=int, help="Target Campaign ID")

    pause_p = campaign_sub.add_parser("pause", help="Transition campaign state to PAUSED")
    pause_p.add_argument("campaign_id", type=int, help="Target Campaign ID")

    resume_p = campaign_sub.add_parser("resume", help="Transition campaign state to RUNNING")
    resume_p.add_argument("campaign_id", type=int, help="Target Campaign ID")

    stop_p = campaign_sub.add_parser("stop", help="Transition campaign state to CANCELLED")
    stop_p.add_argument("campaign_id", type=int, help="Target Campaign ID")
    stop_p.add_argument("--confirm", action="store_true", help="Confirm cancellation")

    # 3. Production Runner Subcommands (Process execution only)
    runner_parser = subparsers.add_parser("runner", help="Production runner daemon lifecycle")
    runner_sub = runner_parser.add_subparsers(dest="subcommand", help="Runner action")

    r_start_p = runner_sub.add_parser("start", help="Start single-campaign production runner daemon")
    r_start_p.add_argument("--campaign-id", type=int, required=True, help="Target Campaign ID to dispatch")
    r_start_p.add_argument("--poll-interval", type=int, default=None, help="Queue polling interval in seconds")
    r_start_p.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    runner_sub.add_parser("status", help="Check active runner process PID and heartbeat")
    runner_sub.add_parser("stop", help="Signal active runner process to stop gracefully")

    # 4. Queue Subcommands
    queue_parser = subparsers.add_parser("queue", help="Queue inspection and manual reconciliation")
    queue_sub = queue_parser.add_subparsers(dest="subcommand", help="Queue action")

    q_stat_p = queue_sub.add_parser("status", help="Display queue counts grouped by status")
    q_stat_p.add_argument("--campaign-id", type=int, default=None, help="Filter by Campaign ID")

    q_insp_p = queue_sub.add_parser("inspect", help="Inspect detailed message metadata")
    q_insp_p.add_argument("message_id", type=int, help="Message ID to inspect")

    q_rec_p = queue_sub.add_parser("reconcile", help="Recover stale leases and list UNKNOWN_OUTCOME items")
    q_rec_p.add_argument("--campaign-id", type=int, default=None, help="Filter by Campaign ID")

    q_over_p = queue_sub.add_parser("override", help="Manually override UNKNOWN_OUTCOME message (requires verified reason)")
    q_over_p.add_argument("message_id", type=int, help="Message ID to override")
    q_over_p.add_argument("--reason", type=str, required=True, help="Explicit explanation of manual verification")

    # 5. Emergency Stop Top-level Commands
    e_stop_p = subparsers.add_parser("emergency-stop", help="Activate global emergency stop killswitch")
    e_stop_p.add_argument("--reason", type=str, default=None, help="Reason for emergency stop activation")

    subparsers.add_parser("emergency-status", help="Inspect global emergency stop status")

    e_res_p = subparsers.add_parser("emergency-resume", help="Deactivate global emergency stop")
    e_res_p.add_argument("--reason", type=str, default=None, help="Reason for emergency resume")

    # 6. Analytics Subcommands
    analytics_parser = subparsers.add_parser("analytics", help="Operational analytics and reporting")
    analytics_sub = analytics_parser.add_subparsers(dest="subcommand", help="Analytics domain")

    a_camp = analytics_sub.add_parser("campaign", help="Campaign performance and Confirmed Send Rate")
    a_camp.add_argument("campaign_id", type=int, help="Target Campaign ID")
    a_camp.add_argument("--json", action="store_true", help="Output raw JSON data")

    a_queue = analytics_sub.add_parser("queue", help="Queue backlog, throughput, and lease distribution")
    a_queue.add_argument("--campaign-id", type=int, default=None, help="Filter by Campaign ID")
    a_queue.add_argument("--json", action="store_true", help="Output raw JSON data")

    a_runner = analytics_sub.add_parser("runner", help="Active runner process, lockfile, and heartbeat stats")
    a_runner.add_argument("--json", action="store_true", help="Output raw JSON data")

    a_prov = analytics_sub.add_parser("provider", help="Provider performance, status, and profile metrics")
    a_prov.add_argument("--json", action="store_true", help="Output raw JSON data")

    a_sys = analytics_sub.add_parser("system", help="Executive summary of overall system and daily quotas")
    a_sys.add_argument("--json", action="store_true", help="Output raw JSON data")

    # 7. Production Preflight Command
    preflight_parser = subparsers.add_parser("preflight", help="Run 10-point production readiness and environment inspection")
    preflight_parser.add_argument("--campaign-id", type=int, default=None, help="Target Campaign ID to inspect readiness for")
    preflight_parser.add_argument("--strict", action="store_true", help="Enforce strict mode (warnings fail check)")
    preflight_parser.add_argument("--json", action="store_true", help="Output raw JSON report")

    # 8. System Subcommands
    system_parser = subparsers.add_parser("system", help="System administration and health inspection")
    system_sub = system_parser.add_subparsers(dest="subcommand", help="System action")

    s_health = system_sub.add_parser("health", help="Live operational system health status")
    s_health.add_argument("--json", action="store_true", help="Output raw JSON health status")

    # 9. Auth Subcommands
    auth_parser = subparsers.add_parser("auth", help="Authentication and account provisioning")
    auth_sub = auth_parser.add_subparsers(dest="subcommand", help="Auth action")

    boot_p = auth_sub.add_parser("bootstrap-owner", help="Bootstrap initial OWNER user account")
    boot_p.add_argument("--username", type=str, default=None, help="Initial OWNER username")
    boot_p.add_argument("--email", type=str, default=None, help="Initial OWNER email")
    boot_p.add_argument("--password", type=str, default=None, help="Initial OWNER password")
    boot_p.add_argument("--password-prompt", action="store_true", help="Prompt securely for password via getpass without echoing")

    return parser


def main(args: Optional[List[str]] = None, db_session=None) -> int:
    """
    Main CLI entrypoint.
    Returns integer exit code.
    """
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    if not parsed_args.command:
        parser.print_help()
        return int(ExitCode.SUCCESS)

    db = db_session if db_session is not None else SessionLocal()
    should_close_db = db_session is None

    try:
        cmd = parsed_args.command

        # Session Routing
        if cmd == "session":
            sub = parsed_args.subcommand
            if sub == "login":
                return int(handle_session_login(parsed_args, db))
            elif sub == "status":
                return int(handle_session_status(parsed_args, db))
            elif sub == "logout":
                return int(handle_session_logout(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        # Campaign Routing
        elif cmd == "campaign":
            sub = parsed_args.subcommand
            if sub == "run":
                return int(handle_campaign_run(parsed_args, db))
            elif sub == "status":
                return int(handle_campaign_status(parsed_args, db))
            elif sub == "pause":
                return int(handle_campaign_pause(parsed_args, db))
            elif sub == "resume":
                return int(handle_campaign_resume(parsed_args, db))
            elif sub == "stop":
                return int(handle_campaign_stop(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        # Runner Routing
        elif cmd == "runner":
            sub = parsed_args.subcommand
            if sub == "start":
                return int(handle_runner_start(parsed_args, db))
            elif sub == "status":
                return int(handle_runner_status(parsed_args, db))
            elif sub == "stop":
                return int(handle_runner_stop(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        # Queue Routing
        elif cmd == "queue":
            sub = parsed_args.subcommand
            if sub == "status":
                return int(handle_queue_status(parsed_args, db))
            elif sub == "inspect":
                return int(handle_queue_inspect(parsed_args, db))
            elif sub == "reconcile":
                return int(handle_queue_reconcile(parsed_args, db))
            elif sub == "override":
                return int(handle_queue_override(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        # Emergency Routing
        elif cmd == "emergency-stop":
            return int(handle_emergency_stop(parsed_args, db))
        elif cmd == "emergency-status":
            return int(handle_emergency_status(parsed_args, db))
        elif cmd == "emergency-resume":
            return int(handle_emergency_resume(parsed_args, db))

        # Analytics Routing
        elif cmd == "analytics":
            sub = parsed_args.subcommand
            if sub == "campaign":
                return int(handle_analytics_campaign(parsed_args, db))
            elif sub == "queue":
                return int(handle_analytics_queue(parsed_args, db))
            elif sub == "runner":
                return int(handle_analytics_runner(parsed_args, db))
            elif sub == "provider":
                return int(handle_analytics_provider(parsed_args, db))
            elif sub == "system":
                return int(handle_analytics_system(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        # Preflight Routing
        elif cmd == "preflight":
            return int(handle_preflight(parsed_args, db))

        # System Routing
        elif cmd == "system":
            sub = parsed_args.subcommand
            if sub == "health":
                return int(handle_system_health(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        # Auth Routing
        elif cmd == "auth":
            sub = parsed_args.subcommand
            if sub == "bootstrap-owner":
                return int(handle_auth_bootstrap_owner(parsed_args, db))
            else:
                parser.print_help()
                return int(ExitCode.INVALID_ARGUMENT)

        else:
            parser.print_help()
            return int(ExitCode.INVALID_ARGUMENT)

    except Exception as e:
        print_error(f"Unexpected operational error: {e}")
        logger.error(f"CLI error: {e}", exc_info=True)
        return int(ExitCode.GENERAL_ERROR)
    finally:
        if should_close_db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
