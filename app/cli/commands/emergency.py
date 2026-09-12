"""
Emergency Stop CLI Commands.

Operational interface to the Phase 3 EmergencyStop mechanism.
Propagates killswitch signal across all workers to halt new message claims (<500ms target)
without abruptly corrupting in-flight browser sends.
"""

import logging
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import print_header, print_card, print_warning_box, print_success, print_info
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.scheduler.emergency_stop import EmergencyStop

logger = logging.getLogger(__name__)


def handle_emergency_stop(args, db: Session) -> ExitCode:
    """
    Executes 'outreach emergency-stop [--reason <text>]'.
    Activates the system-wide emergency stop flag.
    """
    reason = getattr(args, "reason", None) or "Operator triggered emergency stop via CLI"
    print_header("Emergency Stop Activated", "System Killswitch")

    controller = EmergencyStop(db)
    controller.trigger(reason=reason)

    print_warning_box(
        "EMERGENCY STOP IS NOW ACTIVE SYSTEM-WIDE.\n\n"
        "Behavior:\n"
        "- The emergency stop signal has propagated to all workers.\n"
        "- All NEW message claims are blocked immediately (<500ms target).\n"
        "- Any send currently in-flight will finish safely to prevent browser corruption.\n"
        "- Unconfirmed dispatches will be classified as UNKNOWN_OUTCOME.\n"
        "- To resume operations: outreach emergency-resume",
        title="EMERGENCY STOP ENGAGED"
    )

    return ExitCode.SUCCESS


def handle_emergency_status(args, db: Session) -> ExitCode:
    """
    Executes 'outreach emergency-status'.
    Displays whether emergency stop is engaged, timestamp, and last audit record.
    """
    print_header("Emergency Stop Status")

    controller = EmergencyStop(db)
    active = controller.is_active()

    setting = db.query(AppSetting).filter(AppSetting.key == EmergencyStop.SETTING_KEY).first()
    last_log = (
        db.query(AuditLog)
        .filter(AuditLog.event_type.in_(["EMERGENCY_STOP_ACTIVATED", "EMERGENCY_STOP_RESUMED"]))
        .order_by(AuditLog.id.desc())
        .first()
    )

    status_str = "ACTIVE (SYSTEM HALTED)" if active else "INACTIVE (OPERATIONAL)"
    card_data = {
        "Status": status_str,
        "Setting Key": EmergencyStop.SETTING_KEY,
        "Setting Value": setting.value if setting else "false",
        "Last Event": last_log.event_type if last_log else "None",
        "Last Event Time": str(last_log.created_at) if last_log else "None",
        "Last Reason/Detail": (last_log.error_message or last_log.result) if last_log else "None",
    }
    print_card("Emergency Control State", card_data)

    if active:
        print_info("Active stop flag prevents any production runner from claiming messages.")
    else:
        print_info("System is operating normally; message claims are permitted.")

    return ExitCode.SUCCESS


def handle_emergency_resume(args, db: Session) -> ExitCode:
    """
    Executes 'outreach emergency-resume [--reason <text>]'.
    Clears the emergency stop flag.
    """
    reason = getattr(args, "reason", None) or "Operator resumed system via CLI"
    print_header("Emergency Stop Resumed")

    controller = EmergencyStop(db)
    if not controller.is_active():
        print_info("Emergency stop is not currently active.")
        return ExitCode.SUCCESS

    controller.resume(reason=reason)
    print_success("Emergency stop cleared. Normal queue dispatching may now resume.")
    print_info("Note: Campaigns paused by operator or circuit breaker remain in PAUSED status.")
    return ExitCode.SUCCESS
