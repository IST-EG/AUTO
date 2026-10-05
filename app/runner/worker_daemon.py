"""
Always-On Oracle Worker Control Plane Daemon.

Decouples host-level worker availability, worker identity publication,
infrastructure heartbeat, and operational command processing from
campaign execution.

Guarantees:
1. Always-on 24/7 service on Oracle ARM64 host.
2. Publishes stable worker identity to 'system:worker_identity'.
3. Emits 15-second infrastructure heartbeat to 'system:worker_heartbeat'.
4. Polls operational commands every 5s from 'system:whatsapp_command:active'.
5. Executes diagnostic PREFLIGHT safely (120s lease, zero Chrome cold-start,
   zero WhatsApp Web connection, zero messages sent).
6. Recovers orphaned commands on boot.
7. Passive campaign supervision: observes 'system:desired_runner_state'.
   - NEVER changes campaign status.
   - NEVER promotes DRAFT/SCHEDULED/PAUSED/etc. to RUNNING.
   - NEVER auto-starts Campaign 1.
   - If desired state is RUNNING but target campaign is DRAFT/PAUSED/etc.,
     logs warning and stays in STANDBY.
   - Only supervises ProductionRunner if campaign is already explicitly RUNNING.
8. Independent process locks:
   - WorkerDaemon uses WORKER_LOCK_FILE ('data/worker.lock').
   - ProductionRunner uses RUNNER_LOCK_FILE ('data/runner.lock').
9. Graceful shutdown: handles SIGINT / SIGTERM cleanly without force-killing
   child processes or Chrome.
"""

import os
import sys
import time
import logging
import subprocess
from datetime import datetime, timezone
from typing import Optional, Callable, Dict, Any
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.campaign import Campaign
from app.models.app_setting import AppSetting
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.runner.signals import SignalCoordinator
from app.runner.whatsapp_command_handler import (
    WhatsAppCommandHandler,
    validate_worker_instance_id,
)
from app.scheduler.emergency_stop import EmergencyStop
from app.cli.exit_codes import ExitCode
from app.utils.settings import settings

logger = logging.getLogger(__name__)


class WorkerDaemon:
    """
    Always-on Oracle Worker Control Plane Daemon.
    
    Operates as the authoritative host daemon on the execution worker VM.
    Maintains control-plane connectivity, publishes telemetry, processes
    operational commands, and observes campaign execution state without
    conflating infrastructure availability with campaign execution.
    """

    def __init__(
        self,
        db: Optional[Session] = None,
        db_factory: Optional[Callable[[], Session]] = None,
        worker_instance_id: Optional[str] = None,
        poll_interval: Optional[int] = None,
        heartbeat_interval: Optional[int] = None,
        lock_file: Optional[str] = None,
        runner_spawner: Optional[Callable[[int], Any]] = None,
    ):
        self.db = db
        self.db_factory = db_factory
        self.poll_interval = poll_interval or getattr(settings, "WORKER_POLL_INTERVAL_SECONDS", 5)
        self.heartbeat_interval = heartbeat_interval or getattr(settings, "RUNNER_HEARTBEAT_SECONDS", 15)
        self.lock_file = lock_file or getattr(settings, "WORKER_LOCK_FILE", "./data/worker.lock")
        self.runner_spawner = runner_spawner

        # Resolve worker instance ID
        raw_id = worker_instance_id if worker_instance_id is not None else getattr(settings, "WORKER_INSTANCE_ID", "")
        if isinstance(raw_id, str) and raw_id.strip() and validate_worker_instance_id(raw_id.strip()):
            self.worker_instance_id = raw_id.strip()
        else:
            self.worker_instance_id = "unconfigured"

        # Independent process lock for WorkerDaemon singularity (db=None prevents writing to system:active_runner)
        self.process_lock = ProcessLock(db=None, lock_file_path=self.lock_file)
        self.signals = SignalCoordinator()

        self._active_runner_process: Optional[Any] = None
        self._is_running = False

    def _get_db(self):
        """Returns a database session and a boolean indicating if it should be closed."""
        if self.db is not None:
            return self.db, False
        if self.db_factory is not None:
            return self.db_factory(), True
        return SessionLocal(), True

    def start(self, max_iterations: Optional[int] = None) -> ExitCode:
        """
        Starts the always-on worker daemon loop.
        max_iterations is optionally used for bounded testing.
        Returns an ExitCode integer upon completion.
        """
        # 1. Authoritative worker OS file lock
        acquired = self.process_lock.acquire(worker_id=self.worker_instance_id, campaign_id=None)
        if not acquired:
            logger.error("Authoritative worker file lock acquisition failed: another worker daemon is active.")
            return ExitCode.CONCURRENCY_ERROR

        # 2. Register signal handlers for clean SIGINT / SIGTERM interception
        self.signals.register_handlers()

        logger.info(f"WorkerDaemon started cleanly (Instance ID: {self.worker_instance_id}, PID: {os.getpid()}).")
        self._is_running = True

        try:
            # 3. Startup initialization: orphan recovery and initial publications
            db, should_close = self._get_db()
            try:
                command_handler = WhatsAppCommandHandler(db=db, worker_id=self.worker_instance_id, provider=None)
                command_handler.recover_orphans()
                command_handler.publish_identity()
                command_handler._publish_worker_heartbeat(runner_state="STANDBY")
            finally:
                if should_close:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    db.close()

            # 4. Main always-on daemon loop
            iterations = 0
            last_heartbeat_time = time.time()
            last_poll_time = 0.0

            while not self.signals.shutdown_requested:
                if max_iterations is not None and iterations >= max_iterations:
                    logger.info(f"Reached max_iterations limit ({max_iterations}). Halting worker daemon loop.")
                    break

                iterations += 1
                now = time.time()

                db, should_close = self._get_db()
                try:
                    command_handler = WhatsAppCommandHandler(db=db, worker_id=self.worker_instance_id, provider=None)

                    # A. Periodic Infrastructure Heartbeat & Identity Refresh (every 15s)
                    if now - last_heartbeat_time >= self.heartbeat_interval:
                        runner_state = "RUNNING" if self._is_runner_active() else "STANDBY"
                        command_handler._publish_worker_heartbeat(runner_state=runner_state)
                        command_handler.publish_identity()
                        self.process_lock.update_heartbeat()
                        last_heartbeat_time = now

                    # B. Operational Command Polling & Execution (every 5s)
                    if now - last_poll_time >= self.poll_interval:
                        command_handler.poll_and_execute()
                        last_poll_time = now

                    # C. Passive Campaign Supervision & Observation
                    self._supervise_campaign(db)

                except Exception as e:
                    logger.error(f"Error during worker daemon tick: {e}", exc_info=True)
                finally:
                    if should_close:
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        db.close()

                # Small interruptible sleep step
                self._sleep_interruptible(1.0)

            return ExitCode.SUCCESS

        except Exception as e:
            logger.critical(f"Unhandled exception in worker daemon: {e}", exc_info=True)
            return ExitCode.GENERAL_ERROR

        finally:
            self._shutdown_gracefully()

    def _supervise_campaign(self, db: Session) -> None:
        """
        Passive campaign supervision and observation.

        Inspects system:desired_runner_state from the control plane:
        - NEVER changes campaign status in the database.
        - NEVER promotes DRAFT/SCHEDULED/PAUSED/etc. to RUNNING.
        - NEVER auto-starts Campaign 1 or any campaign merely because WorkerDaemon is running.
        - If desired state is RUNNING: checks target campaign status in DB.
          If target campaign is NOT in RUNNING status (e.g. DRAFT):
            Logs warning and leaves runner in STANDBY.
          If target campaign IS in RUNNING status:
            Can supervise/launch ProductionRunner for that campaign.
        - If desired state is STOPPED:
          Requests clean shutdown of any supervised runner child.
        """
        # CRITICAL SAFETY CHECK: Emergency stop check
        if EmergencyStop(db).is_active():
            if self._is_runner_active():
                logger.warning("Emergency stop is active. Stopping supervised runner child.")
                self._stop_supervised_runner()
            return

        desired_state_row = db.query(AppSetting).filter(AppSetting.key == "system:desired_runner_state").first()
        desired_state = desired_state_row.value if desired_state_row else "STOPPED"

        if desired_state != "RUNNING":
            # Desired state is STOPPED or not set -> runner should be stopped
            if self._active_runner_process is not None:
                self._stop_supervised_runner()
            return

        # Desired state is RUNNING: check desired campaign ID
        desired_camp_row = db.query(AppSetting).filter(AppSetting.key == "system:desired_runner_campaign_id").first()
        if not desired_camp_row or not desired_camp_row.value or not desired_camp_row.value.isdigit():
            logger.debug("Desired runner state is RUNNING but no valid campaign_id is configured.")
            return

        campaign_id = int(desired_camp_row.value)

        # Check campaign status in database
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            logger.warning(f"Desired runner target Campaign {campaign_id} does not exist in database.")
            return

        # CRITICAL SAFETY CHECK: NEVER promote or run a non-RUNNING campaign
        if campaign.status != "RUNNING":
            logger.info(
                f"Cannot engage runner: Target Campaign {campaign_id} is in status '{campaign.status}', "
                f"must be 'RUNNING'. Runner remains in STANDBY."
            )
            return

        # If campaign is RUNNING and no runner is currently active, spawn runner child
        if not self._is_runner_active():
            self._start_supervised_runner(campaign_id)

    def _start_supervised_runner(self, campaign_id: int) -> None:
        """Spawns the ProductionRunner process for an explicitly RUNNING campaign."""
        if self.runner_spawner is not None:
            logger.info(f"WorkerDaemon invoking custom runner spawner for Campaign {campaign_id}.")
            try:
                self._active_runner_process = self.runner_spawner(campaign_id)
            except Exception as e:
                logger.error(f"Custom runner spawner failed for Campaign {campaign_id}: {e}", exc_info=True)
            return

        cmd = [
            sys.executable,
            "-m",
            "app.cli.main",
            "runner",
            "start",
            "--campaign-id",
            str(campaign_id),
        ]
        logger.info(f"WorkerDaemon spawning ProductionRunner for Campaign {campaign_id}: {' '.join(cmd)}")
        try:
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
            self._active_runner_process = proc
        except Exception as e:
            logger.error(f"Failed to spawn ProductionRunner for Campaign {campaign_id}: {e}", exc_info=True)

    def _stop_supervised_runner(self) -> None:
        """
        Gracefully requests the active ProductionRunner to stop.
        Does NOT force-kill or os.kill the runner or Chrome.
        Uses database coordination (system:desired_runner_state = STOPPED).
        """
        logger.info("Requesting active ProductionRunner to stop cleanly via database coordination.")
        db, should_close = self._get_db()
        try:
            now = datetime.now(timezone.utc)
            row = db.query(AppSetting).filter(AppSetting.key == "system:desired_runner_state").first()
            if not row:
                row = AppSetting(
                    key="system:desired_runner_state",
                    value="STOPPED",
                    description="Desired production runner state",
                    updated_at=now,
                )
                db.add(row)
            else:
                row.value = "STOPPED"
                row.updated_at = now
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to set desired_runner_state to STOPPED: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            if should_close:
                try:
                    db.rollback()
                except Exception:
                    pass
                db.close()

    def _is_runner_active(self) -> bool:
        """Checks if a ProductionRunner process is currently active."""
        if self._active_runner_process is not None:
            if hasattr(self._active_runner_process, "poll"):
                poll_res = self._active_runner_process.poll()
                if poll_res is None:
                    return True
                else:
                    self._active_runner_process = None
            else:
                # Custom spawner representation (e.g. PID or object)
                return True

        # Check runner process lock file (authoritative on host)
        runner_lock = ProcessLock(db=None, lock_file_path=settings.RUNNER_LOCK_FILE)
        return runner_lock.get_active_process_info() is not None

    def _sleep_interruptible(self, duration: float) -> None:
        """Sleeps in small increments to respond rapidly to shutdown signals."""
        step = 0.25
        elapsed = 0.0
        while elapsed < duration and not self.signals.shutdown_requested:
            sleep_time = min(step, duration - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time

    def _shutdown_gracefully(self) -> None:
        """Executes clean graceful shutdown sequence."""
        logger.info("WorkerDaemon shutting down gracefully...")
        self._is_running = False

        # 1. Update heartbeat to STOPPED in database
        try:
            db, should_close = self._get_db()
            try:
                command_handler = WhatsAppCommandHandler(db=db, worker_id=self.worker_instance_id, provider=None)
                command_handler._publish_worker_heartbeat(runner_state="STOPPED")
            finally:
                if should_close:
                    db.close()
        except Exception as e:
            logger.warning(f"Failed to update final heartbeat on shutdown: {e}")

        # 2. Release authoritative OS worker lock
        try:
            self.process_lock.release()
        except Exception as e:
            logger.warning(f"Error releasing worker process lock: {e}")

        # 3. Restore signal handlers
        self.signals.restore_handlers()
        logger.info("WorkerDaemon shutdown complete.")
