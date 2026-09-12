"""
CLI Preflight Command Handler.

Provides:
  - outreach preflight [--strict] [--json]
"""

import json
from typing import Any
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import (
    print_header,
    print_card,
    print_table,
    print_success,
    print_error,
    print_warning,
)
from app.readiness.preflight import run_preflight


def handle_preflight(args: Any, db: Session) -> ExitCode:
    """Runs preflight environment and readiness inspection."""
    strict = getattr(args, "strict", False)
    campaign_id = getattr(args, "campaign_id", None)

    result = run_preflight(db=db, campaign_id=campaign_id, strict=strict)

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return result.exit_code

    print_header(
        "Production Preflight & Readiness Inspection",
        f"Mode: {'STRICT' if strict else 'STANDARD'}",
    )

    table_rows = []
    for c in result.checks:
        status_symbol = "[PASS]" if c.passed else "[FAIL]" if c.critical else "[WARN]"
        table_rows.append([c.name, status_symbol, c.message])

    print_table(["Check Category", "Status", "Details"], table_rows)

    if result.passed:
        print_success("Preflight inspection PASSED. System is ready for production runner execution.")
    else:
        print_error(f"Preflight inspection FAILED with exit code {int(result.exit_code)}.")

    return result.exit_code
