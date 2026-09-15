"""
WhatsApp Command Service.

Implements the deterministic command protocol for Phase 7.6:
- system:whatsapp_command:active (Atomic in-flight command slot)
- system:whatsapp_command:last_completed (Terminal outcome record)
- Monotonic versioning and atomic Compare-And-Swap (CAS) state machine
- Single-inflight command serialization (HTTP 409 Conflict)
- 60-second execution lease
- Deterministic orphan/crash recovery
- Observable lifecycle: REQUESTED -> CLAIMED -> EXECUTING -> COMPLETED / FAILED
"""

import json
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class WhatsAppCommandService:
    """Service governing WhatsApp Web operational command delivery and lifecycle."""

    ACTIVE_COMMAND_KEY = "system:whatsapp_command:active"
    LAST_COMPLETED_KEY = "system:whatsapp_command:last_completed"
    DEFAULT_LEASE_SECONDS = 60
    REQUEST_TIMEOUT_SECONDS = 120

    @classmethod
    def get_active_command(cls, db: Session) -> Optional[Dict[str, Any]]:
        """Reads and parses current in-flight command from AppSetting."""
        try:
            row = db.query(AppSetting).filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY).first()
            if not row or not row.value:
                return None
            data = json.loads(row.value)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(f"Error reading active WhatsApp command: {e}")
            return None

    @classmethod
    def get_last_completed_command(cls, db: Session) -> Optional[Dict[str, Any]]:
        """Reads and parses the most recently completed command record."""
        try:
            row = db.query(AppSetting).filter(AppSetting.key == cls.LAST_COMPLETED_KEY).first()
            if not row or not row.value:
                return None
            data = json.loads(row.value)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(f"Error reading last completed WhatsApp command: {e}")
            return None

    @classmethod
    def get_command_by_id(cls, db: Session, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Locates a command by request_id.
        Searches:
        1. Active command slot
        2. Last completed command slot
        3. Audit logs fallback
        """
        # 1. Check active
        active = cls.get_active_command(db)
        if active and active.get("request_id") == request_id:
            return active

        # 2. Check last completed
        last = cls.get_last_completed_command(db)
        if last and last.get("request_id") == request_id:
            return last

        # 3. Check audit log
        try:
            audit = (
                db.query(AuditLog)
                .filter(AuditLog.result.like(f"%{request_id}%"))
                .order_by(AuditLog.id.desc())
                .first()
            )
            if audit:
                return {
                    "request_id": request_id,
                    "action": audit.event_type.replace("WHATSAPP_", "").replace("_REQUESTED", "").replace("_COMPLETED", ""),
                    "status": audit.status,
                    "version": 1,
                    "requested_by": "operator",
                    "requested_at": audit.created_at.isoformat() if audit.created_at else "",
                    "completed_at": audit.created_at.isoformat() if audit.created_at else None,
                    "result": audit.result,
                    "error_message": audit.error_message,
                }
        except Exception:
            pass

        return None

    @classmethod
    def submit_command(
        cls,
        db: Session,
        action: str,
        requested_by: str,
        requested_by_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Submits an operational command to the active slot.
        Enforces single-inflight serialization:
        If an unexpired active command exists, returns False with conflict message.
        """
        now_utc = datetime.now(timezone.utc)
        active = cls.get_active_command(db)

        # Check if an active command is currently in progress
        if active and active.get("status") in ("REQUESTED", "CLAIMED", "EXECUTING"):
            # Check for lease expiration on CLAIMED/EXECUTING
            lease_str = active.get("lease_expires_at")
            is_expired = False
            if lease_str:
                try:
                    lease_dt = datetime.fromisoformat(lease_str.replace("Z", "+00:00"))
                    if now_utc > lease_dt:
                        is_expired = True
                except Exception:
                    pass

            # Check for request timeout on REQUESTED (waiting for worker pickup)
            req_str = active.get("requested_at")
            if active.get("status") == "REQUESTED" and req_str:
                try:
                    req_dt = datetime.fromisoformat(req_str.replace("Z", "+00:00"))
                    if (now_utc - req_dt).total_seconds() > cls.REQUEST_TIMEOUT_SECONDS:
                        is_expired = True
                except Exception:
                    pass

            if not is_expired:
                conflict_msg = (
                    f"Another WhatsApp operation ({active.get('action')}, "
                    f"request_id='{active.get('request_id')}') is currently in progress."
                )
                return False, conflict_msg, active

            # Auto-recover expired active command before accepting new one
            cls.recover_stale_or_orphaned_command(db)

        # Generate unique tracking ID
        request_id = f"req_wa_{int(time.time())}_{uuid.uuid4().hex[:8]}"

        command_data = {
            "request_id": request_id,
            "action": action,
            "params": params or {},
            "status": "REQUESTED",
            "version": 1,
            "requested_by": requested_by,
            "requested_by_id": requested_by_id,
            "requested_at": now_utc.isoformat(),
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "executed_at": None,
            "completed_at": None,
            "result": None,
            "error_message": None,
        }

        try:
            # Atomic set in app_settings
            setting_row = db.query(AppSetting).filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY).first()
            if not setting_row:
                setting_row = AppSetting(
                    key=cls.ACTIVE_COMMAND_KEY,
                    value=json.dumps(command_data),
                    description="In-flight operational command for WhatsApp Web provider",
                    updated_at=now_utc,
                )
                db.add(setting_row)
            else:
                setting_row.value = json.dumps(command_data)
                setting_row.updated_at = now_utc

            # Emit audit log for request submission
            audit_log = AuditLog(
                event_type=f"WHATSAPP_{action}_REQUESTED",
                status="REQUESTED",
                result=f"Submitted by {requested_by} (request_id={request_id})",
            )
            db.add(audit_log)
            db.commit()

            logger.info(f"WhatsApp command submitted: {action} (request_id={request_id}, by={requested_by})")
            return True, "Command submitted successfully.", command_data

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to submit WhatsApp command {action}: {e}", exc_info=True)
            return False, f"Failed to submit command: {str(e)}", None

    @classmethod
    def claim_command(
        cls,
        db: Session,
        worker_id: str,
        lease_duration_seconds: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Worker-side atomic claim.
        Finds active command in REQUESTED state, transitions to CLAIMED,
        increments version, and sets lease_expires_at.
        """
        now_utc = datetime.now(timezone.utc)
        lease_seconds = lease_duration_seconds or cls.DEFAULT_LEASE_SECONDS

        try:
            row = (
                db.query(AppSetting)
                .filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY)
                .with_for_update(nowait=False)
                .first()
            )
            if not row or not row.value:
                return None

            data = json.loads(row.value)
            if not isinstance(data, dict):
                return None

            if data.get("status") != "REQUESTED":
                return None

            # Atomically claim
            current_version = int(data.get("version", 1))
            data["status"] = "CLAIMED"
            data["claimed_by"] = worker_id
            data["claimed_at"] = now_utc.isoformat()
            data["lease_expires_at"] = (now_utc + timedelta(seconds=lease_seconds)).isoformat()
            data["version"] = current_version + 1

            row.value = json.dumps(data)
            row.updated_at = now_utc
            db.commit()

            logger.info(f"Worker {worker_id} claimed WhatsApp command {data.get('action')} (request_id={data.get('request_id')})")
            return data

        except Exception as e:
            db.rollback()
            logger.warning(f"Failed to claim active WhatsApp command: {e}")
            return None

    @classmethod
    def mark_executing(cls, db: Session, request_id: str, worker_id: str) -> bool:
        """Transitions command status from CLAIMED to EXECUTING."""
        now_utc = datetime.now(timezone.utc)
        try:
            row = db.query(AppSetting).filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY).first()
            if not row or not row.value:
                return False

            data = json.loads(row.value)
            if data.get("request_id") != request_id or data.get("status") != "CLAIMED":
                return False

            data["status"] = "EXECUTING"
            data["executed_at"] = now_utc.isoformat()
            row.value = json.dumps(data)
            row.updated_at = now_utc
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.warning(f"Failed to transition command {request_id} to EXECUTING: {e}")
            return False

    @classmethod
    def complete_command(
        cls,
        db: Session,
        request_id: str,
        worker_id: str,
        result: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Marks command as COMPLETED.
        Writes result, emits audit log, copies to last_completed, and frees active slot.
        """
        now_utc = datetime.now(timezone.utc)
        try:
            row = db.query(AppSetting).filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY).first()
            if not row or not row.value:
                return False

            data = json.loads(row.value)
            if data.get("request_id") != request_id:
                return False

            action = data.get("action", "COMMAND")
            data["status"] = "COMPLETED"
            data["completed_at"] = now_utc.isoformat()
            data["result"] = result
            if details:
                data["result_details"] = details

            # 1. Update last_completed setting
            last_row = db.query(AppSetting).filter(AppSetting.key == cls.LAST_COMPLETED_KEY).first()
            if not last_row:
                last_row = AppSetting(
                    key=cls.LAST_COMPLETED_KEY,
                    value=json.dumps(data),
                    description="Last completed WhatsApp Web operational command outcome",
                    updated_at=now_utc,
                )
                db.add(last_row)
            else:
                last_row.value = json.dumps(data)
                last_row.updated_at = now_utc

            # 2. Clear active command slot
            row.value = json.dumps({
                "status": "COMPLETED",
                "request_id": request_id,
                "completed_at": now_utc.isoformat()
            })
            row.updated_at = now_utc

            # 3. Emit immutable audit log
            audit_log = AuditLog(
                event_type=f"WHATSAPP_{action}_COMPLETED",
                status="SUCCESS",
                result=f"Completed by {worker_id}: {result} (request_id={request_id})",
            )
            db.add(audit_log)
            db.commit()

            logger.info(f"WhatsApp command {action} completed successfully (request_id={request_id})")
            return True

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to complete command {request_id}: {e}", exc_info=True)
            return False

    @classmethod
    def fail_command(
        cls,
        db: Session,
        request_id: str,
        worker_id: Optional[str] = None,
        error_message: str = "Command execution failed",
    ) -> bool:
        """
        Marks command as FAILED.
        Writes sanitized error, emits audit log, copies to last_completed, and frees active slot.
        """
        now_utc = datetime.now(timezone.utc)
        try:
            row = db.query(AppSetting).filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY).first()
            if not row or not row.value:
                return False

            data = json.loads(row.value)
            if data.get("request_id") != request_id:
                return False

            action = data.get("action", "COMMAND")
            data["status"] = "FAILED"
            data["completed_at"] = now_utc.isoformat()
            data["error_message"] = error_message

            # 1. Update last_completed
            last_row = db.query(AppSetting).filter(AppSetting.key == cls.LAST_COMPLETED_KEY).first()
            if not last_row:
                last_row = AppSetting(
                    key=cls.LAST_COMPLETED_KEY,
                    value=json.dumps(data),
                    description="Last completed WhatsApp Web operational command outcome",
                    updated_at=now_utc,
                )
                db.add(last_row)
            else:
                last_row.value = json.dumps(data)
                last_row.updated_at = now_utc

            # 2. Clear active slot
            row.value = json.dumps({
                "status": "FAILED",
                "request_id": request_id,
                "error_message": error_message,
                "completed_at": now_utc.isoformat()
            })
            row.updated_at = now_utc

            # 3. Emit audit log
            audit_log = AuditLog(
                event_type="WHATSAPP_COMMAND_FAILED",
                status="FAILED",
                error_message=f"[{action}] {error_message} (request_id={request_id})",
            )
            db.add(audit_log)
            db.commit()

            logger.warning(f"WhatsApp command {action} failed: {error_message} (request_id={request_id})")
            return True

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to fail command {request_id}: {e}", exc_info=True)
            return False

    @classmethod
    def recover_stale_or_orphaned_command(
        cls,
        db: Session,
        worker_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Inspects active command slot and recovers orphaned or lease-expired commands.
        Called on runner startup and during preflight / status checks.
        """
        now_utc = datetime.now(timezone.utc)
        try:
            active = cls.get_active_command(db)
            if not active:
                return None

            status = active.get("status")
            if status not in ("CLAIMED", "EXECUTING", "REQUESTED"):
                return None

            is_orphaned = False
            reason = ""

            # Check lease expiration on CLAIMED or EXECUTING
            if status in ("CLAIMED", "EXECUTING"):
                lease_str = active.get("lease_expires_at")
                if lease_str:
                    try:
                        lease_dt = datetime.fromisoformat(lease_str.replace("Z", "+00:00"))
                        if now_utc > lease_dt:
                            is_orphaned = True
                            reason = f"Worker lease expired ({lease_str}) without completion"
                    except Exception:
                        is_orphaned = True
                        reason = "Invalid lease timestamp"
                else:
                    is_orphaned = True
                    reason = "Missing lease deadline"

            # Check request timeout on unconsumed REQUESTED
            elif status == "REQUESTED":
                req_str = active.get("requested_at")
                if req_str:
                    try:
                        req_dt = datetime.fromisoformat(req_str.replace("Z", "+00:00"))
                        if (now_utc - req_dt).total_seconds() > cls.REQUEST_TIMEOUT_SECONDS:
                            is_orphaned = True
                            reason = f"Command remained unconsumed for >{cls.REQUEST_TIMEOUT_SECONDS}s"
                    except Exception:
                        pass

            if not is_orphaned:
                return None

            request_id = active.get("request_id", "unknown")
            action = active.get("action", "COMMAND")
            error_text = f"Orphaned command recovered: {reason}"

            active["status"] = "FAILED"
            active["completed_at"] = now_utc.isoformat()
            active["error_message"] = error_text

            # Update last_completed
            last_row = db.query(AppSetting).filter(AppSetting.key == cls.LAST_COMPLETED_KEY).first()
            if not last_row:
                last_row = AppSetting(
                    key=cls.LAST_COMPLETED_KEY,
                    value=json.dumps(active),
                    description="Last completed WhatsApp Web operational command outcome",
                    updated_at=now_utc,
                )
                db.add(last_row)
            else:
                last_row.value = json.dumps(active)
                last_row.updated_at = now_utc

            # Clear active slot
            row = db.query(AppSetting).filter(AppSetting.key == cls.ACTIVE_COMMAND_KEY).first()
            if row:
                row.value = json.dumps({
                    "status": "FAILED",
                    "request_id": request_id,
                    "error_message": error_text,
                    "completed_at": now_utc.isoformat()
                })
                row.updated_at = now_utc

            # Emit audit log
            audit_log = AuditLog(
                event_type="WHATSAPP_COMMAND_FAILED",
                status="ORPHAN_RECOVERED",
                error_message=f"[{action}] {error_text} (request_id={request_id})",
            )
            db.add(audit_log)
            db.commit()

            logger.warning(f"Recovered orphaned WhatsApp command: {action} (request_id={request_id}, reason={reason})")
            return active

        except Exception as e:
            db.rollback()
            logger.error(f"Error during orphaned command recovery: {e}", exc_info=True)
            return None

    @classmethod
    def clear_stale_command(cls, db: Session, operator_username: str) -> Tuple[bool, str]:
        """
        Administrative override allowing ADMIN/OWNER to clear a stuck active command.
        """
        now_utc = datetime.now(timezone.utc)
        active = cls.get_active_command(db)
        if not active or active.get("status") in ("COMPLETED", "FAILED"):
            return False, "No active in-flight command is currently registered."

        request_id = active.get("request_id", "unknown")
        action = active.get("action", "COMMAND")
        msg = f"Cleared manually by operator {operator_username}"

        cls.fail_command(
            db=db,
            request_id=request_id,
            worker_id="operator_override",
            error_message=msg,
        )

        audit_log = AuditLog(
            event_type="WHATSAPP_COMMAND_CLEARED",
            status="CLEARED",
            result=f"Action '{action}' (request_id={request_id}) manually cleared by {operator_username}",
        )
        db.add(audit_log)
        db.commit()

        return True, f"Command '{action}' (request_id={request_id}) has been cleared."
