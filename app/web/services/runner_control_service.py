"""
Runner Control Service.

Mediates process supervisor control between the Web Control Center and the
ProductionRunner daemon. Guarantees that:
1. The OS file lock (data/runner.lock) remains the authoritative singularity mechanism.
2. The Web API cannot start duplicate runners.
3. Terminations use SIGTERM to allow in-flight sends to finish safely.
4. All operational actions emit immutable audit log records.
"""

import os
import sys
import json
import time
import signal
import subprocess
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, Callable
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.scheduler.emergency_stop import EmergencyStop
from app.readiness.preflight import run_preflight
from app.utils.settings import settings
from app.utils.logger import get_logger

logger = get_logger("runner_control")


class RunnerControlService:
    """Service mediating runner operations and authoritative lock inspection."""

    DESIRED_STATE_SETTING_KEY = "system:desired_runner_state"
    DESIRED_CAMPAIGN_SETTING_KEY = "system:desired_runner_campaign_id"

    @classmethod
    def get_desired_state(cls, db: Session) -> Dict[str, Any]:
        """Returns the desired runner state and target campaign from AppSetting via batched query."""
        from app.services.app_setting_service import AppSettingService
        try:
            settings_map = AppSettingService.get_many(
                db, [cls.DESIRED_STATE_SETTING_KEY, cls.DESIRED_CAMPAIGN_SETTING_KEY]
            )
            raw_state = settings_map.get(cls.DESIRED_STATE_SETTING_KEY)
            raw_camp = settings_map.get(cls.DESIRED_CAMPAIGN_SETTING_KEY)
            state = raw_state if raw_state else "STOPPED"
            campaign_id = int(raw_camp) if raw_camp and raw_camp.isdigit() else None
            return {"desired_state": state, "desired_campaign_id": campaign_id}
        except Exception:
            return {"desired_state": "STOPPED", "desired_campaign_id": None}

    @classmethod
    def set_desired_state(cls, db: Session, state: str, campaign_id: Optional[int] = None) -> None:
        """Sets the desired runner state and target campaign in AppSetting."""
        now = datetime.now(timezone.utc)
        try:
            state_row = db.query(AppSetting).filter(AppSetting.key == cls.DESIRED_STATE_SETTING_KEY).first()
            if not state_row:
                state_row = AppSetting(
                    key=cls.DESIRED_STATE_SETTING_KEY,
                    value=state,
                    description="Desired production runner operational state (RUNNING or STOPPED)",
                    updated_at=now,
                )
                db.add(state_row)
            else:
                state_row.value = state
                state_row.updated_at = now

            if campaign_id is not None:
                camp_row = db.query(AppSetting).filter(AppSetting.key == cls.DESIRED_CAMPAIGN_SETTING_KEY).first()
                if not camp_row:
                    camp_row = AppSetting(
                        key=cls.DESIRED_CAMPAIGN_SETTING_KEY,
                        value=str(campaign_id),
                        description="Desired target campaign ID for production runner",
                        updated_at=now,
                    )
                    db.add(camp_row)
                else:
                    camp_row.value = str(campaign_id)
                    camp_row.updated_at = now
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to record desired runner state in AppSetting: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    @classmethod
    def enqueue_eligible_contacts(cls, db: Session, campaign: Campaign) -> int:
        """Enqueues all ELIGIBLE campaign contacts into the message queue if not yet enqueued."""
        from app.models.campaign_contact import CampaignContact
        from app.models.contact import Contact
        from app.models.message import Message
        from app.queue.service import PersistentQueueService
        from app.queue.state_machine import QueueState
        from app.campaigns.template_service import MessageTemplateService

        eligible_ccs = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.status == "ELIGIBLE"
        ).all()

        if not eligible_ccs:
            return 0

        queue_svc = PersistentQueueService(db)
        now_utc = datetime.now(timezone.utc)
        enqueued_count = 0

        for cc in eligible_ccs:
            existing_msg = db.query(Message).filter(
                Message.campaign_contact_id == cc.id
            ).first()
            if not existing_msg:
                contact = db.query(Contact).filter(Contact.id == cc.contact_id).first()
                if contact:
                    try:
                        rendered = MessageTemplateService.render_message(
                            campaign.message_template, contact, campaign
                        )
                    except Exception:
                        rendered = campaign.message_template or "Hello"

                    queue_svc.enqueue_message(
                        campaign_id=campaign.id,
                        contact_id=contact.id,
                        rendered_content=rendered,
                        campaign_contact_id=cc.id,
                        sequence_number=1,
                        initial_state=QueueState.QUEUED
                    )
                    cc.status = "QUEUED"
                    cc.enqueued_at = now_utc
                    enqueued_count += 1

        if enqueued_count > 0:
            db.commit()
        return enqueued_count

    @classmethod
    def get_status(cls, db: Session) -> Dict[str, Any]:
        """
        Inspects authoritative OS file lock and database heartbeat.
        Returns a dictionary representation matching RunnerStatusDTO.
        """
        desired_info = cls.get_desired_state(db)
        desired_state = desired_info["desired_state"]
        desired_campaign_id = desired_info["desired_campaign_id"]

        process_lock = ProcessLock(db=db)
        runner_info = process_lock.get_active_runner_info()

        if not runner_info:
            # Also check if lock file exists on disk with dead PID
            lock_data = process_lock.read_lock_file()
            if lock_data and lock_data.get("pid"):
                dead_pid = lock_data["pid"]
                if not is_pid_alive(dead_pid):
                    return {
                        "is_running": False,
                        "state": "STALE_LOCK_DETECTED",
                        "pid": dead_pid,
                        "worker_id": lock_data.get("worker_id"),
                        "campaign_id": lock_data.get("campaign_id"),
                        "started_at": lock_data.get("started_at"),
                        "last_heartbeat": lock_data.get("last_heartbeat"),
                        "heartbeat_age_seconds": None,
                        "uptime_seconds": 0.0,
                        "is_stale": True,
                        "lock_authoritative": True,
                        "desired_state": desired_state,
                        "desired_campaign_id": desired_campaign_id,
                    }

            return {
                "is_running": False,
                "state": "STOPPED",
                "pid": None,
                "worker_id": None,
                "campaign_id": None,
                "started_at": None,
                "last_heartbeat": None,
                "heartbeat_age_seconds": None,
                "uptime_seconds": 0.0,
                "is_stale": False,
                "lock_authoritative": True,
                "desired_state": desired_state,
                "desired_campaign_id": desired_campaign_id,
            }

        pid = runner_info.get("pid")
        alive = is_pid_alive(pid) if pid else False

        now_utc = datetime.now(timezone.utc)
        heartbeat_str = runner_info.get("heartbeat_at") or runner_info.get("last_heartbeat") or runner_info.get("started_at")
        heartbeat_age: Optional[float] = None
        if heartbeat_str:
            try:
                hb_dt = datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                heartbeat_age = max(0.0, (now_utc - hb_dt).total_seconds())
            except Exception:
                pass

        is_remote = bool(os.environ.get("VERCEL")) or getattr(settings, "RUNNER_REMOTE_COORDINATION", False)
        if is_remote and not os.path.exists(process_lock.lock_file_path):
            alive = True

        started_str = runner_info.get("started_at")
        uptime = 0.0
        if started_str:
            try:
                st_dt = datetime.fromisoformat(started_str.replace("Z", "+00:00"))
                uptime = max(0.0, (now_utc - st_dt).total_seconds())
            except Exception:
                pass

        if not alive:
            state = "STALE_LOCK_DETECTED"
        elif heartbeat_age is not None and heartbeat_age > 60:
            state = "UNHEALTHY"
        elif heartbeat_age is not None and heartbeat_age > 30:
            state = "DEGRADED"
        else:
            state = "RUNNING"

        return {
            "is_running": alive,
            "state": state,
            "pid": pid,
            "worker_id": runner_info.get("worker_id"),
            "campaign_id": runner_info.get("campaign_id"),
            "started_at": started_str,
            "last_heartbeat": heartbeat_str,
            "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            "uptime_seconds": round(uptime, 1),
            "is_stale": (heartbeat_age is not None and heartbeat_age > 60) or not alive,
            "lock_authoritative": True,
            "desired_state": desired_state,
            "desired_campaign_id": desired_campaign_id,
        }

    @classmethod
    def start_runner(
        cls,
        db: Session,
        campaign_id: int,
        operator_username: str,
        runner_spawner: Optional[Callable[[int], int]] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validates target campaign, checks safety guards, and coordinates runner start.
        In remote mode (Vercel/coordination), delegates execution to the remote worker daemon via AppSetting.
        In local mode, executes preflight readiness and launches the local background process.
        """
        # 1. Verify campaign exists
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return False, f"Target Campaign {campaign_id} was not found.", None

        # 2. Verify campaign state is RUNNING or resume from PAUSED
        if campaign.status == "PAUSED":
            campaign.status = "RUNNING"
            campaign.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Campaign {campaign_id} automatically resumed to RUNNING for runner start.")
        elif campaign.status != "RUNNING":
            return False, (
                f"Campaign {campaign_id} is not in RUNNING state (current: {campaign.status}). "
                "Only campaigns in RUNNING or PAUSED state may be processed by a runner."
            ), None

        # 3. Check Emergency Stop
        e_stop = EmergencyStop(db=db)
        if e_stop.is_active():
            return False, "Emergency stop is currently ACTIVE. Production runner cannot be started.", None

        # Automatically enqueue any ELIGIBLE contacts before starting dispatch
        try:
            cls.enqueue_eligible_contacts(db, campaign)
        except Exception as e:
            logger.warning(f"Error during pre-dispatch contact enqueue: {e}")
            try:
                db.rollback()
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        is_remote = (
            bool(os.environ.get("VERCEL"))
            or getattr(settings, "RUNNER_REMOTE_COORDINATION", False)
            or not bool(getattr(settings, "WORKER_INSTANCE_ID", None))
        )

        # 4. Remote Coordination Mode
        if is_remote and runner_spawner is None:
            # Check worker heartbeat freshness
            hb_row = db.query(AppSetting).filter(AppSetting.key == "system:worker_heartbeat").first()
            if hb_row and hb_row.value:
                try:
                    hb_data = json.loads(hb_row.value)
                    hb_time_str = hb_data.get("last_seen") or hb_data.get("timestamp")
                    if hb_time_str:
                        hb_dt = datetime.fromisoformat(hb_time_str.replace("Z", "+00:00"))
                        age = (now - hb_dt).total_seconds()
                        if age > 120:
                            return False, f"Cannot start runner: Remote worker daemon heartbeat is stale ({int(age)}s old > 120s limit). Worker appears offline.", None
                except Exception as e:
                    logger.warning(f"Error parsing worker heartbeat: {e}")
            else:
                logger.warning("No worker heartbeat record found in AppSetting; proceeding with desired state coordination.")

            # Check if runner is already active according to database lock
            process_lock = ProcessLock(db=db)
            active_info = process_lock.get_active_runner_info()
            if active_info and active_info.get("pid"):
                hb_str = active_info.get("heartbeat_at") or active_info.get("last_heartbeat") or active_info.get("started_at")
                if hb_str:
                    try:
                        hb_dt = datetime.fromisoformat(hb_str.replace("Z", "+00:00"))
                        if (now - hb_dt).total_seconds() <= 60:
                            return False, (
                                f"Cannot start runner: another production runner process is actively running "
                                f"(PID: {active_info.get('pid')}, Campaign: {active_info.get('campaign_id')})."
                            ), None
                    except Exception:
                        pass

            # Coordinate start via database desired state
            cls.set_desired_state(db=db, state="RUNNING", campaign_id=campaign_id)

            audit = AuditLog(
                event_type="RUNNER_START_REQUESTED",
                status="SUCCESS",
                campaign_id=campaign_id,
                result=json.dumps({
                    "actor": operator_username,
                    "action": "RUNNER_START",
                    "campaign_id": campaign_id,
                    "spawned_pid": None,
                    "mode": "REMOTE_COORDINATION",
                }),
                created_at=now,
            )
            db.add(audit)
            db.commit()

            return True, f"Production runner desired state set to RUNNING for Campaign {campaign_id}. Worker will engage on next poll.", {
                "campaign_id": campaign_id,
                "pid": None,
                "started_at": now.isoformat(),
                "desired_state": "RUNNING",
            }

        # 5. Local Execution Mode (or test spawner)
        process_lock = ProcessLock(db=db)
        active_info = process_lock.get_active_runner_info()
        if active_info and active_info.get("pid"):
            existing_pid = active_info["pid"]
            if is_pid_alive(existing_pid):
                return False, (
                    f"Cannot start runner: another production runner process is actively running "
                    f"(PID: {existing_pid}, Campaign: {active_info.get('campaign_id')}). "
                    "Process singularity permits only one runner per machine."
                ), None
            else:
                logger.info(f"Clearing stale runner lock from dead PID {existing_pid}.")
                process_lock.release()

        # Preflight readiness verification for local execution
        preflight = run_preflight(db=db, campaign_id=campaign_id, strict=False)
        if not preflight.passed:
            def _c_name(c):
                return c.get("name", "Check") if isinstance(c, dict) else getattr(c, "name", "Check")
            def _c_msg(c):
                return c.get("error") or c.get("message", "Failed") if isinstance(c, dict) else getattr(c, "message", getattr(c, "error", "Failed"))
            def _c_failed(c):
                return not c.get("passed", False) if isinstance(c, dict) else not getattr(c, "passed", False)
            failure_reasons = "; ".join(f"{_c_name(c)}: {_c_msg(c)}" for c in preflight.checks if _c_failed(c))
            return False, f"Preflight readiness check failed: {failure_reasons}", None

        cls.set_desired_state(db=db, state="RUNNING", campaign_id=campaign_id)

        try:
            if runner_spawner is not None:
                spawned_pid = runner_spawner(campaign_id)
            else:
                cmd = [
                    sys.executable,
                    "-m",
                    "app.cli.main",
                    "runner",
                    "start",
                    "--campaign-id",
                    str(campaign_id),
                ]
                if os.name == "nt":
                    proc = subprocess.Popen(
                        cmd,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        close_fds=True,
                    )
                else:
                    proc = subprocess.Popen(
                        cmd,
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        close_fds=True,
                    )
                spawned_pid = proc.pid

            audit = AuditLog(
                event_type="RUNNER_START_REQUESTED",
                status="SUCCESS",
                campaign_id=campaign_id,
                result=json.dumps({
                    "actor": operator_username,
                    "action": "RUNNER_START",
                    "campaign_id": campaign_id,
                    "spawned_pid": spawned_pid,
                    "mode": "LOCAL_PROCESS",
                }),
                created_at=now,
            )
            db.add(audit)
            db.commit()

            return True, f"Production runner successfully initiated for Campaign {campaign_id} (PID: {spawned_pid}).", {
                "campaign_id": campaign_id,
                "pid": spawned_pid,
                "started_at": now.isoformat(),
                "desired_state": "RUNNING",
            }

        except Exception as e:
            logger.error(f"Failed to spawn runner process: {e}", exc_info=True)
            return False, f"Failed to spawn runner process: {str(e)}", None

    @classmethod
    def stop_runner(
        cls,
        db: Session,
        operator_username: str,
        timeout_seconds: int = 15,
        stopper_fn: Optional[Callable[[int], None]] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Sends SIGTERM to active runner process to initiate graceful stop,
        or sets desired state to STOPPED in remote coordination mode.
        Waits up to timeout_seconds for in-flight dispatches to reach a safe point.
        """
        cls.set_desired_state(db=db, state="STOPPED")
        is_remote = bool(os.environ.get("VERCEL")) or getattr(settings, "RUNNER_REMOTE_COORDINATION", False)

        process_lock = ProcessLock(db=db)
        active_info = process_lock.get_active_runner_info()

        if not active_info or not active_info.get("pid"):
            if is_remote and stopper_fn is None:
                audit = AuditLog(
                    event_type="RUNNER_STOP_REQUESTED",
                    status="SUCCESS",
                    result=json.dumps({
                        "actor": operator_username,
                        "action": "RUNNER_STOP",
                        "mode": "REMOTE_COORDINATION",
                    }),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(audit)
                db.commit()
                return True, "Desired runner state set to STOPPED.", {
                    "stopped": True,
                    "desired_state": "STOPPED",
                }
            return False, "No active production runner process was detected to stop.", None

        pid = active_info["pid"]
        campaign_id = active_info.get("campaign_id")
        now = datetime.now(timezone.utc)

        if is_remote and stopper_fn is None:
            audit = AuditLog(
                event_type="RUNNER_STOP_REQUESTED",
                status="SUCCESS",
                campaign_id=campaign_id,
                result=json.dumps({
                    "actor": operator_username,
                    "action": "RUNNER_STOP",
                    "pid": pid,
                    "mode": "REMOTE_COORDINATION",
                }),
                created_at=now,
            )
            db.add(audit)
            db.commit()
            return True, "Production runner desired state set to STOPPED. Worker will gracefully shut down after completing in-flight dispatch.", {
                "pid": pid,
                "stopped": True,
                "desired_state": "STOPPED",
            }

        if not is_pid_alive(pid):
            process_lock.release()
            audit = AuditLog(
                event_type="RUNNER_STOP_REQUESTED",
                status="SUCCESS",
                campaign_id=campaign_id,
                result=json.dumps({
                    "actor": operator_username,
                    "action": "RUNNER_STOP",
                    "pid": pid,
                    "note": "Process was already terminated; released stale lock.",
                }),
                created_at=now,
            )
            db.add(audit)
            db.commit()
            return True, f"Runner process (PID {pid}) was already terminated. Stale lock released.", {
                "pid": pid,
                "stopped": True,
            }

        try:
            if stopper_fn is not None:
                stopper_fn(pid)
            else:
                os.kill(pid, signal.SIGTERM)

            # Wait for graceful shutdown
            end_time = time.time() + timeout_seconds
            stopped = False
            while time.time() < end_time:
                if not is_pid_alive(pid):
                    stopped = True
                    break
                time.sleep(0.3)

            if stopped:
                process_lock.release()
                audit = AuditLog(
                    event_type="RUNNER_STOP_REQUESTED",
                    status="SUCCESS",
                    campaign_id=campaign_id,
                    result=json.dumps({
                        "actor": operator_username,
                        "action": "RUNNER_STOP",
                        "pid": pid,
                        "stopped": True,
                    }),
                    created_at=now,
                )
                db.add(audit)
                db.commit()
                return True, f"Production runner (PID {pid}) stopped gracefully.", {
                    "pid": pid,
                    "stopped": True,
                }
            else:
                audit = AuditLog(
                    event_type="RUNNER_STOP_REQUESTED",
                    status="IN_PROGRESS",
                    campaign_id=campaign_id,
                    result=json.dumps({
                        "actor": operator_username,
                        "action": "RUNNER_STOP",
                        "pid": pid,
                        "stopped": False,
                        "note": f"Did not exit within {timeout_seconds}s; in-flight send may be completing.",
                    }),
                    created_at=now,
                )
                db.add(audit)
                db.commit()
                return True, (
                    f"Stop signal sent to runner (PID {pid}); waiting for in-flight operation "
                    "to reach safe cancellation point."
                ), {
                    "pid": pid,
                    "stopped": False,
                }

        except ProcessLookupError:
            process_lock.release()
            return True, f"Runner process {pid} already exited.", {"pid": pid, "stopped": True}
        except Exception as e:
            logger.error(f"Error signaling runner PID {pid}: {e}", exc_info=True)
            return False, f"Could not stop runner PID {pid}: {str(e)}", None
