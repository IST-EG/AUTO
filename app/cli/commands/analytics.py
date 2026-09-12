"""
CLI Analytics Command Handlers.

Provides:
  - outreach analytics campaign <id> [--json]
  - outreach analytics queue [--campaign-id <id>] [--json]
  - outreach analytics runner [--json]
  - outreach analytics provider [--json]
  - outreach analytics system [--json]
"""

import json
import sys
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import (
    print_header,
    print_card,
    print_table,
    print_info,
    print_error,
    print_warning,
    print_success,
)
from app.services.analytics_service import AnalyticsService


def handle_analytics_campaign(args: Any, db: Session) -> ExitCode:
    """Displays comprehensive campaign analytics, contacts breakdown, and Confirmed Send Rate."""
    campaign_id = getattr(args, "campaign_id", None)
    if not campaign_id:
        print_error("Campaign ID is required.")
        return ExitCode.INVALID_ARGUMENT

    data = AnalyticsService.get_campaign_analytics(db, campaign_id)
    if not data:
        print_error(f"Campaign {campaign_id} not found.")
        return ExitCode.NOT_FOUND

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return ExitCode.SUCCESS

    print_header(
        f"Campaign Analytics: {data['name']} (ID: {data['campaign_id']})",
        f"Status: {data['status']}",
    )

    # 1. Summary Card
    duration_str = f"{data['duration_seconds']:.1f}s" if data["duration_seconds"] is not None else "N/A"
    summary_card = {
        "Campaign ID": data["campaign_id"],
        "Name": data["name"],
        "Status": data["status"],
        "Started At": data["started_at"] or "Not started",
        "Completed At": data["completed_at"] or "In progress / Active",
        "Duration": duration_str,
        "Total Contacts": data["total_contacts"],
    }
    print_card("Overview", summary_card)

    # 2. Contacts Breakdown Table
    cb = data["contacts_breakdown"]
    contact_rows = [
        ["Eligible for Outreach", cb["eligible"]],
        ["Excluded (Limits/Rules)", cb["excluded"]],
        ["Pending Evaluation", cb["pending"]],
    ]
    print_table(["Contact Category", "Count"], contact_rows)

    # 3. Message Queue Lifecycle Breakdown Table
    qb = data["queue_breakdown"]
    queue_rows = [
        ["QUEUED (Ready for Claim)", qb["queued"]],
        ["PROCESSING (Lease Active)", qb["processing"]],
        ["RETRY_PENDING (Backoff)", qb["retry_pending"]],
        ["SENT (Confirmed on UI)", qb["confirmed_sends"]],
        ["FAILED (Definitive Error)", qb["failed"]],
        ["UNKNOWN_OUTCOME (Blocked)", qb["unknown_outcome"]],
        ["SKIPPED", qb["skipped"]],
        ["CANCELLED", qb["cancelled"]],
    ]
    print_table(["Queue Lifecycle State", "Count"], queue_rows)

    # 4. Performance & Confirmed Send Rate Card
    perf = data["performance"]
    perf_card = {
        "CONFIRMED SEND RATE": f"{perf['confirmed_send_rate']:.2f}%",
        "Failure Rate": f"{perf['failure_rate']:.2f}%",
        "Completion Percentage": f"{perf['completion_percentage']:.2f}%",
        "Terminal Dispatches": perf["terminal_dispatches"],
    }
    print_card("Performance Metrics", perf_card)
    print_info(
        "Semantic Note: 'Confirmed Send Rate' reflects internal send confirmation via "
        "WhatsApp Web UI checkmarks, NOT recipient device delivery or read receipts."
    )

    if qb["unknown_outcome"] > 0:
        print_warning(
            f"ALERT: {qb['unknown_outcome']} message(s) in UNKNOWN_OUTCOME. "
            "Manual reconciliation is required via 'outreach queue override'."
        )

    return ExitCode.SUCCESS


def handle_analytics_queue(args: Any, db: Session) -> ExitCode:
    """Displays queue depth, processing leases, stale leases, and throughput."""
    campaign_id = getattr(args, "campaign_id", None)
    data = AnalyticsService.get_queue_analytics(db, campaign_id)

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return ExitCode.SUCCESS

    scope_title = f"Campaign {campaign_id}" if campaign_id else "Global Queue"
    print_header(f"Queue Analytics ({scope_title})")

    # 1. Backlog Table
    b = data["backlog"]
    backlog_rows = [
        ["Queued (Awaiting Worker)", b["queued"]],
        ["Processing (Worker Leases)", b["processing"]],
        ["Retry Pending (Backoff Wait)", b["retry_pending"]],
        ["Stale Leases (>300s)", b["stale_leases"]],
    ]
    print_table(["Backlog Vector", "Count"], backlog_rows)

    # 2. Terminal Counts Table
    t = data["terminal"]
    terminal_rows = [
        ["Confirmed Sends", t["confirmed_sends"]],
        ["Permanent Failures", t["failed"]],
        ["Unknown Outcomes (Blocked)", t["unknown_outcome"]],
        ["Skipped", t["skipped"]],
        ["Cancelled", t["cancelled"]],
    ]
    print_table(["Terminal State", "Count"], terminal_rows)

    # 3. Throughput Card
    tp = data["confirmed_throughput"]
    tp_card = {
        "Confirmed Sends (Last 1h)": tp["last_1_hour"],
        "Confirmed Sends (Last 6h)": tp["last_6_hours"],
        "Confirmed Sends (Last 24h)": tp["last_24_hours"],
    }
    print_card("Confirmed Dispatch Throughput", tp_card)

    # Warnings
    hw = data["health_warnings"]
    if hw["has_stale_leases"]:
        print_warning(f"ALERT: {b['stale_leases']} stale worker lease(s) detected. Run 'outreach queue reconcile'.")
    if hw["has_unknown_outcome"]:
        print_warning(f"ALERT: {t['unknown_outcome']} UNKNOWN_OUTCOME message(s) require operator inspection.")

    return ExitCode.SUCCESS


def handle_analytics_runner(args: Any, db: Session) -> ExitCode:
    """Displays active runner status, liveliness, heartbeat age, and session stats."""
    data = AnalyticsService.get_runner_analytics(db)

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return ExitCode.SUCCESS

    print_header("Production Runner Analytics")

    runner_card = {
        "State": data["state"],
        "Is Active / Alive": "YES" if data["is_running"] else "NO",
        "PID": data["pid"] or "N/A",
        "Worker ID": data["worker_id"] or "N/A",
        "Target Campaign ID": data["campaign_id"] or "N/A",
        "Started At": data["started_at"] or "N/A",
        "Uptime": f"{data['uptime_seconds']:.1f}s" if data["uptime_seconds"] else "0.0s",
        "Heartbeat Age": f"{data['heartbeat_age_seconds']:.1f}s" if data["heartbeat_age_seconds"] is not None else "N/A",
        "Dispatches Completed": data["dispatches_completed"],
    }
    print_card("Runner Status", runner_card)

    if data["is_stale"]:
        print_warning("WARNING: Runner heartbeat is older than 60 seconds. Process may be degraded or unresponsive.")

    return ExitCode.SUCCESS


def handle_analytics_provider(args: Any, db: Session) -> ExitCode:
    """Displays provider session profile status, error distribution, and send rate."""
    data = AnalyticsService.get_provider_analytics(db)

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return ExitCode.SUCCESS

    print_header(f"Provider Analytics ({data['provider_name']})")

    # 1. Session Profile Card
    sess = data["session"]
    sess_card = {
        "Profile Path": sess["profile_path"],
        "Directory Exists": "YES" if sess["profile_dir_exists"] else "NO",
        "Session Data Present": "YES" if sess["has_profile_data"] else "NO (Login Required)",
        "Headless Mode": "YES" if sess["headless_mode"] else "NO (Visual Mode)",
    }
    print_card("WhatsApp Web Session Profile", sess_card)

    # 2. Dispatch Distribution Table
    dist = data["dispatch_distribution"]
    dist_rows = [
        ["Total Attempted Messages", dist["total_attempted_messages"]],
        ["Confirmed Sends (UI Checkmark)", dist["confirmed_sends"]],
        ["Temporary Errors / Retries", dist["temporary_retry_count"]],
        ["Permanent Failures", dist["permanent_failures"]],
        ["Unknown Outcomes (Disrupted)", dist["unknown_outcomes"]],
    ]
    print_table(["Dispatch Category", "Count"], dist_rows)

    # 3. Overall Confirmed Rate
    print_card("Provider Reliability", {"Confirmed Send Rate": f"{data['confirmed_send_rate']:.2f}%"})

    return ExitCode.SUCCESS


def handle_analytics_system(args: Any, db: Session) -> ExitCode:
    """Displays high-level executive dashboard combining all system vectors."""
    data = AnalyticsService.get_system_analytics(db)

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return ExitCode.SUCCESS

    print_header("Outreach System Executive Dashboard")

    # 1. System Health & E-Stop Card
    e_stop = data["emergency_stop"]
    e_stop_status = "ACTIVE (DISPATCHES BLOCKED)" if e_stop["active"] else "INACTIVE (DISPATCHES PERMITTED)"
    system_card = {
        "Emergency Stop": e_stop_status,
        "Active Runner State": data["active_runner"]["state"],
        "Active Runner PID": data["active_runner"]["pid"] or "None",
    }
    if e_stop["reason"]:
        system_card["E-Stop Reason"] = e_stop["reason"]
    print_card("Operational Status", system_card)

    # 2. Queue Overview Card
    qs = data["queue_summary"]
    q_card = {
        "Queue Backlog Depth": qs["depth"],
        "In-Flight Leases": qs["processing"],
        "Retry Pending Backlog": qs["retry_backlog"],
        "Stale Leases": qs["stale_leases"],
        "Pending Unknown Outcomes": qs["unknown_outcomes"],
        "Confirmed Sends (24h)": qs["confirmed_24h"],
    }
    print_card("Queue Backlog & Throughput", q_card)

    # 3. Daily Quota Card
    dq = data["daily_quota"]
    quota_card = {
        "Messages Sent Today": dq["sent_today"],
        "Global Daily Limit": dq["global_daily_limit"],
        "Capacity Remaining": dq["capacity_remaining"],
    }
    print_card("Daily Compliance Quota", quota_card)

    # 4. Campaigns Summary Table
    camp = data["campaigns"]
    camp_rows = [
        ["Total Campaigns", camp["total"]],
        ["Running Campaigns", camp["running"]],
        ["Paused Campaigns", camp["paused"]],
        ["Completed Campaigns", camp["completed"]],
    ]
    print_table(["Campaign Lifecycle Status", "Count"], camp_rows)

    return ExitCode.SUCCESS
