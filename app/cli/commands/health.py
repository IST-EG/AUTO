"""
CLI System Health Command Handler.

Provides:
  - outreach system health [--json]
"""

import json
from typing import Any
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import (
    print_header,
    print_card,
    print_success,
    print_error,
    print_warning,
    print_info,
)
from app.readiness.health import evaluate_system_health, HealthState


def handle_system_health(args: Any, db: Session) -> ExitCode:
    """Evaluates and reports live operational system health."""
    result = evaluate_system_health(db)

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return ExitCode.SUCCESS

    print_header(
        f"Operational System Health: {result.state.value}",
    )

    card_data = {
        "Overall Health State": result.state.value,
        "Database Responsive": "YES" if result.details["database_responsive"] else "NO",
        "Emergency Stop": "ACTIVE" if result.details["emergency_stop_active"] else "INACTIVE",
        "Active Runner State": result.details["runner"].get("state", "STOPPED"),
        "Queue Backlog": result.details["queue"]["depth"],
        "Stale Leases": result.details["queue"]["stale_leases"],
        "Unknown Outcomes": result.details["queue"]["unknown_outcomes"],
    }
    print_card("Health Indicators", card_data)

    if result.reasons:
        print_info("Status Details:")
        for r in result.reasons:
            if result.state == HealthState.UNHEALTHY:
                print_error(f"  * {r}")
            elif result.state == HealthState.DEGRADED:
                print_warning(f"  * {r}")
            else:
                print_info(f"  * {r}")

    if result.state == HealthState.HEALTHY:
        print_success("System is HEALTHY and operating normally.")
    elif result.state == HealthState.DEGRADED:
        print_warning("System is DEGRADED. Monitoring is advised.")
    elif result.state == HealthState.UNHEALTHY:
        print_error("System is UNHEALTHY. Immediate operator intervention is required.")

    return ExitCode.SUCCESS
