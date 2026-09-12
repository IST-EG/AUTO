"""
Runtime Health Evaluation Engine.

Evaluates operational health into four distinct states:
  - HEALTHY
  - DEGRADED
  - UNHEALTHY
  - STOPPED
"""

from enum import Enum
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.message import Message
from app.models.app_setting import AppSetting
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.services.analytics_service import AnalyticsService


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    STOPPED = "STOPPED"


class HealthResult:
    def __init__(
        self,
        state: HealthState,
        reasons: List[str],
        details: Dict[str, Any],
    ):
        self.state = state
        self.reasons = reasons
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "reasons": self.reasons,
            "details": self.details,
        }


def evaluate_system_health(db: Session) -> HealthResult:
    """
    Evaluates real-time system health across database, runner, queue, and emergency stop.
    """
    reasons: List[str] = []
    unhealthy_flags: List[str] = []
    degraded_flags: List[str] = []

    # 1. Database check
    db_responsive = False
    try:
        db.execute(text("SELECT 1"))
        db_responsive = True
    except Exception as e:
        unhealthy_flags.append(f"Database unresponsive: {e}")

    # 2. Emergency Stop check
    e_stop_active = False
    e_stop_reason = None
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == "emergency_stop").first()
        if setting and setting.value:
            import json
            val = json.loads(setting.value)
            if val.get("active"):
                e_stop_active = True
                e_stop_reason = val.get("reason", "Operator emergency stop active")
    except Exception:
        pass

    # 3. Queue issues check
    stale_leases = 0
    unknown_outcomes = 0
    queue_depth = 0
    if db_responsive:
        try:
            now_utc = datetime.now(timezone.utc)
            stale_cutoff = now_utc - timedelta(seconds=300)
            stale_leases = (
                db.query(Message)
                .filter(Message.status == "PROCESSING", Message.locked_at < stale_cutoff)
                .count()
            )
            unknown_outcomes = (
                db.query(Message)
                .filter(Message.status == "FAILED", Message.error_type == "UNKNOWN_OUTCOME")
                .count()
            )
            queue_depth = (
                db.query(Message)
                .filter(Message.status == "QUEUED")
                .count()
            )
        except Exception:
            pass

    if stale_leases > 0:
        unhealthy_flags.append(f"{stale_leases} stale worker lease(s) detected")

    if unknown_outcomes > 0:
        degraded_flags.append(f"{unknown_outcomes} unreconciled UNKNOWN_OUTCOME message(s) pending operator action")

    if queue_depth > 1000:
        degraded_flags.append(f"Elevated queue backlog: {queue_depth} queued messages")

    # 4. Runner liveliness and heartbeat check
    runner_analytics = AnalyticsService.get_runner_analytics(db) if db_responsive else {}
    is_runner_active = runner_analytics.get("is_running", False)
    runner_state = runner_analytics.get("state", "STOPPED")
    heartbeat_age = runner_analytics.get("heartbeat_age_seconds")

    if is_runner_active:
        if heartbeat_age is not None:
            if heartbeat_age > 60:
                unhealthy_flags.append(f"Runner heartbeat stale ({heartbeat_age:.1f}s > 60s)")
            elif heartbeat_age > 30:
                degraded_flags.append(f"Runner heartbeat lagging ({heartbeat_age:.1f}s)")
    else:
        # If lockfile exists but PID is dead, it's UNHEALTHY
        lock_info = ProcessLock(db=db).read_lock_file() if db_responsive else None
        if lock_info and lock_info.get("pid"):
            if not is_pid_alive(lock_info["pid"]):
                unhealthy_flags.append(f"Orphaned lock detected from dead PID {lock_info['pid']}")

    # 5. Determine state
    if unhealthy_flags:
        final_state = HealthState.UNHEALTHY
        reasons = unhealthy_flags + degraded_flags
    elif degraded_flags:
        final_state = HealthState.DEGRADED
        reasons = degraded_flags
    elif e_stop_active:
        final_state = HealthState.STOPPED
        reasons = [f"Emergency stop engaged: {e_stop_reason}"]
    elif not is_runner_active:
        final_state = HealthState.STOPPED
        reasons = ["No production runner daemon actively running"]
    else:
        final_state = HealthState.HEALTHY
        reasons = ["All subsystems operational; runner active with fresh heartbeat"]

    return HealthResult(
        state=final_state,
        reasons=reasons,
        details={
            "database_responsive": db_responsive,
            "emergency_stop_active": e_stop_active,
            "runner": runner_analytics,
            "queue": {
                "depth": queue_depth,
                "stale_leases": stale_leases,
                "unknown_outcomes": unknown_outcomes,
            },
        },
    )
