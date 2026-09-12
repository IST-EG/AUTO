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
        """Returns the desired runner state and target campaign from AppSetting."""
        try:
            state_row = db.query(AppSetting).filter(AppSetting.key == cls.DESIRED_STATE_SETTING_KEY).first()
            camp_row = db.query(AppSetting).filter(AppSetting.key == cls.DESIRED_CAMPAIGN_SETTING_KEY).first()
            state = state_row.value if state_row else "STOPPED"
            campaign_id = int(camp_row.value) if camp_row and camp_row.value.isdigit() else None
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
        Validates target campaign and OS file lock, executes preflight readiness,
        and launches the ProductionRunner background process.
        """
        # 1. Verify campaign exists
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return False, f"Target Campaign {campaign_id} was not found.", None

        # 2. Verify campaign state is RUNNING
        if campaign.status != "RUNNING":
            return False, (
                f"Campaign {campaign_id} is not in RUNNING state (current: {campaign.status}). "
                "Only campaigns in RUNNING state may be processed by a runner."
            ), None

        # 3. Check authoritative OS file lock singularity
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
                # Stale lock recovery
                logger.info(f"Clearing stale runner lock from dead PID {existing_pid}.")
                process_lock.release()

        # 4. Preflight readiness verification
        preflight = run_preflight(db=db, campaign_id=campaign_id, strict=False)
        if not preflight.passed:
            failure_reasons = "; ".join(f"{c['name']}: {c['error']}" for c in preflight.checks if not c["passed"])
            return False, f"Preflight readiness check failed: {failure_reasons}", None

        # 5. Spawn background runner process (or coordinate remote worker)
        now = datetime.now(timezone.utc)
        cls.set_desired_state(db=db, state="RUNNING", campaign_id=campaign_id)
        is_remote = bool(os.environ.get("VERCEL")) or getattr(settings, "RUNNER_REMOTE_COORDINATION", False)

        try:
            if runner_spawner is not None:
                spawned_pid = runner_spawner(campaign_id)
            elif is_remote:
                spawned_pid = None
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

            # 6. Record Audit Log
            audit = AuditLog(
                event_type="RUNNER_START_REQUESTED",
                status="SUCCESS",
                campaign_id=campaign_id,
                result=json.dumps({
                    "actor": operator_username,
                    "action": "RUNNER_START",
                    "campaign_id": campaign_id,
                    "spawned_pid": spawned_pid,
                    "mode": "REMOTE_COORDINATION" if is_remote and runner_spawner is None else "LOCAL_PROCESS",
                }),
                created_at=now,
            )
            db.add(audit)
            db.commit()

            msg = (
                f"Production runner desired state set to RUNNING for Campaign {campaign_id}. Worker will engage on next poll."
                if is_remote and runner_spawner is None
                else f"Production runner successfully initiated for Campaign {campaign_id} (PID: {spawned_pid})."
            )

            return True, msg, {
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
