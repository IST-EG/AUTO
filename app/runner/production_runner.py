"""
Single-Campaign Production Runner Daemon.

Orchestrates long-running worker dispatch for a single active campaign.
Enforces:
- Authoritative OS file lock to prevent duplicate runners.
- Signal interception (SIGINT, SIGTERM) for deterministic graceful shutdown.
- Provider readiness verification before message dispatch.
- Emergency stop propagation (<500ms target) to prevent new claims.
- Safe cancellation points protecting in-flight sends.
- Startup stale lease recovery.
- Continuous heartbeat tracking in lockfile and database.
"""

import time
import random
import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.queue.service import PersistentQueueService
from app.scheduler.worker import QueueWorker
from app.scheduler.emergency_stop import EmergencyStop
from app.scheduler.circuit_breaker import CircuitBreaker
from app.providers.base import MessageProvider
from app.runner.process_lock import ProcessLock
from app.runner.signals import SignalCoordinator
from app.runner.lifecycle import RunnerLifecycle, RunnerLifecycleState
from app.cli.exit_codes import ExitCode
from app.utils.settings import settings

logger = logging.getLogger(__name__)


class ProductionRunner:
    """
    Long-running production daemon managing a QueueWorker for a single campaign.
    """

    def __init__(
        self,
        db: Session,
        campaign_id: int,
        provider: Optional[MessageProvider] = None,
        poll_interval: Optional[int] = None,
        lock_file: Optional[str] = None,
        worker_id: Optional[str] = None,
    ):
        self.db = db
        self.campaign_id = campaign_id
        self.provider = provider
        self.poll_interval = poll_interval or settings.RUNNER_POLL_INTERVAL_SECONDS
        self.worker_id = worker_id or f"runner_camp{campaign_id}_{hex(int(time.time()))[2:]}"

        self.lifecycle = RunnerLifecycle()
        self.process_lock = ProcessLock(db=db, lock_file_path=lock_file)
        self.signals = SignalCoordinator()
        self.emergency_stop = EmergencyStop(db)
        self.circuit_breaker = CircuitBreaker(db)
        self.queue_service = PersistentQueueService(db)
        from app.runner.whatsapp_command_handler import WhatsAppCommandHandler
        self.whatsapp_command_handler = WhatsAppCommandHandler(db=db, worker_id=self.worker_id, provider=self.provider)

        self._last_heartbeat_time = 0.0
        self._last_stats_log_time = 0.0
        self._worker: Optional[QueueWorker] = None

    def start(self, max_iterations: Optional[int] = None) -> ExitCode:
        """
        Starts the production runner daemon loop.
        max_iterations is optionally used for bounded testing.
        Returns an ExitCode integer upon completion.
        """
        self.lifecycle.transition_to(RunnerLifecycleState.STARTING, "Initializing runner")

        # 1. Authoritative OS file lock
        acquired = self.process_lock.acquire(worker_id=self.worker_id, campaign_id=self.campaign_id)
        if not acquired:
            logger.error("Authoritative OS file lock acquisition failed: another runner is active.")
            self.lifecycle.transition_to(RunnerLifecycleState.FAILED, "Concurrency error: duplicate runner")
            return ExitCode.CONCURRENCY_ERROR

        # 2. Register signal handlers
        self.signals.register_handlers()

        try:
            # 3. Preflight readiness check
            from app.readiness.preflight import run_preflight
            preflight = run_preflight(db=self.db, campaign_id=self.campaign_id, strict=False, provider=self.provider)
            if not preflight.passed:
                logger.error(f"Preflight readiness check failed with exit code {int(preflight.exit_code)}.")
                self.lifecycle.transition_to(RunnerLifecycleState.FAILED, f"Preflight check failed: {preflight.exit_code}")
                return preflight.exit_code

            # 4. Verify target campaign exists and is RUNNING
            campaign = self.db.query(Campaign).filter(Campaign.id == self.campaign_id).first()
            if not campaign:
                logger.error(f"Target Campaign {self.campaign_id} not found.")
                self.lifecycle.transition_to(RunnerLifecycleState.FAILED, "Campaign not found")
                return ExitCode.NOT_FOUND

            if campaign.status != "RUNNING":
                logger.error(f"Target Campaign {self.campaign_id} is in status '{campaign.status}', must be 'RUNNING'.")
                self.lifecycle.transition_to(RunnerLifecycleState.FAILED, f"Campaign status {campaign.status}")
                return ExitCode.INVALID_STATE

            # 4. Initialize and verify provider readiness
            if self.provider is None:
                from app.providers.whatsapp_web.provider import WhatsAppWebProvider
                self.provider = WhatsAppWebProvider(
                    session_path=settings.WHATSAPP_SESSION_PATH,
                    headless=settings.WHATSAPP_HEADLESS,
                    browser_timeout=settings.WHATSAPP_BROWSER_TIMEOUT,
                    qr_timeout=settings.WHATSAPP_QR_TIMEOUT,
                    chrome_binary=settings.WHATSAPP_CHROME_BINARY or None,
                    chromedriver_path=settings.WHATSAPP_CHROMEDRIVER_PATH or None,
                )
            self.whatsapp_command_handler.provider = self.provider

            logger.info("Verifying provider connection and health...")
            self.lifecycle.transition_to(RunnerLifecycleState.AUTHENTICATING, "Connecting provider")

            try:
                self.provider.connect()
            except Exception as e:
                logger.error(f"Provider connection error: {e}")
                self.lifecycle.transition_to(RunnerLifecycleState.FAILED, f"Provider connect error: {e}")
                return ExitCode.PROVIDER_UNAVAILABLE

            if not self.provider.health_check():
                logger.error("Provider health check failed: session not authenticated or browser unresponsive.")
                self.lifecycle.transition_to(RunnerLifecycleState.FAILED, "Provider health check failed")
                return ExitCode.AUTHENTICATION_REQUIRED

            # 5. Startup stale lease recovery & command orphan recovery
            recovered_leases = self.queue_service.recover_stale_leases()
            if recovered_leases > 0:
                logger.info(f"Startup reconciliation: recovered {recovered_leases} stale message leases.")
            self.whatsapp_command_handler.recover_orphans()
            self.whatsapp_command_handler.publish_telemetry()

            # 6. Initialize QueueWorker
            self._worker = QueueWorker(
                db=self.db,
                provider=self.provider,
                worker_id=self.worker_id,
                apply_pacing_delay=False  # Runner manages loop pacing
            )

            # Record RUNNER_STARTED audit event
            self._log_audit_event("RUNNER_STARTED", status="RUNNING", result=f"Started for Campaign {self.campaign_id}")

            self.lifecycle.transition_to(RunnerLifecycleState.RUNNING, f"Active dispatch on Campaign {self.campaign_id}")
            logger.info(f"Production runner started cleanly for Campaign {self.campaign_id} (Worker: {self.worker_id}).")

            # 7. Main Polling Loop
            iterations = 0
            while not self.signals.shutdown_requested:
                if max_iterations is not None and iterations >= max_iterations:
                    logger.info(f"Reached max_iterations limit ({max_iterations}). Halting loop.")
                    break

                iterations += 1
                now_mono = time.time()

                # Maintain periodic heartbeat
                if now_mono - self._last_heartbeat_time >= settings.RUNNER_HEARTBEAT_SECONDS:
                    self.process_lock.update_heartbeat()
                    self.whatsapp_command_handler.publish_telemetry()
                    self._last_heartbeat_time = now_mono

                # Process any pending operational WhatsApp commands
                self.whatsapp_command_handler.poll_and_execute()

                # Check safe cancellation point 0: Desired runner state from DB coordination
                desired_setting = self.db.query(AppSetting).filter(AppSetting.key == "system:desired_runner_state").first()
                if desired_setting and desired_setting.value == "STOPPED":
                    logger.info("Desired runner state is STOPPED. Initiating graceful loop termination.")
                    break

                # Check safe cancellation point 1: Emergency Stop
                if self.emergency_stop.is_active():
                    if self.lifecycle.current_state != RunnerLifecycleState.PAUSED:
                        self.lifecycle.transition_to(RunnerLifecycleState.PAUSED, "Emergency stop active")
                        logger.warning("Emergency stop active. Pausing queue claims.")
                    self._sleep_interruptible(self.poll_interval)
                    continue

                # Refresh campaign record and check campaign state
                self.db.expire(campaign)
                campaign = self.db.query(Campaign).filter(Campaign.id == self.campaign_id).first()
                if not campaign or campaign.status != "RUNNING":
                    if self.lifecycle.current_state != RunnerLifecycleState.PAUSED:
                        self.lifecycle.transition_to(
                            RunnerLifecycleState.PAUSED,
                            f"Campaign status is '{campaign.status if campaign else 'DELETED'}'"
                        )
                        logger.info(f"Campaign {self.campaign_id} is no longer RUNNING. Pausing worker loop.")

                    if not campaign or campaign.status in ("COMPLETED", "CANCELLED", "FAILED"):
                        logger.info(f"Campaign {self.campaign_id} entered terminal state. Stopping runner.")
                        break

                    self._sleep_interruptible(self.poll_interval)
                    continue

                # Check Circuit Breaker consecutive failures
                consecutive_errs = self.circuit_breaker.get_consecutive_errors(self.campaign_id)
                if consecutive_errs >= campaign.error_threshold:
                    if self.lifecycle.current_state != RunnerLifecycleState.PAUSED:
                        self.lifecycle.transition_to(
                            RunnerLifecycleState.PAUSED,
                            f"Circuit breaker tripped ({consecutive_errs} errors)"
                        )
                        logger.warning(f"Circuit breaker tripped for Campaign {self.campaign_id}. Halting dispatches.")
                    self._sleep_interruptible(self.poll_interval)
                    continue

                # Resume RUNNING state if previously paused
                if self.lifecycle.current_state == RunnerLifecycleState.PAUSED:
                    self.lifecycle.transition_to(RunnerLifecycleState.RUNNING, "Resuming dispatches")

                # Process next message for this campaign
                processed_msg = self._worker.process_next_message(campaign_id=self.campaign_id)

                if processed_msg is not None:
                    # Message was processed; apply pacing delay
                    min_delay = max(0, campaign.min_delay_seconds if campaign.min_delay_seconds is not None else 1)
                    max_delay = max(min_delay, campaign.max_delay_seconds if campaign.max_delay_seconds is not None else 1)
                    if max_delay > 0:
                        pacing_delay = random.uniform(min_delay, max_delay)
                        logger.debug(f"Pacing delay: sleeping {pacing_delay:.2f}s before next dispatch.")
                        self._sleep_interruptible(pacing_delay)
                else:
                    # Queue is idle for this campaign
                    if self.lifecycle.current_state != RunnerLifecycleState.IDLE:
                        self.lifecycle.transition_to(RunnerLifecycleState.IDLE, "No eligible messages in queue")
                    self._sleep_interruptible(self.poll_interval)

            return ExitCode.SUCCESS

        except Exception as e:
            logger.critical(f"Unhandled exception in production runner: {e}", exc_info=True)
            self.lifecycle.transition_to(RunnerLifecycleState.FAILED, str(e))
            self._log_audit_event("RUNNER_FAILED", status="FAILED", error=str(e))
            return ExitCode.GENERAL_ERROR

        finally:
            self._shutdown_gracefully()

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
        self.lifecycle.transition_to(RunnerLifecycleState.STOPPING, "Executing graceful shutdown")
        logger.info("Executing graceful shutdown sequence...")

        # 1. Disconnect provider cleanly
        if self.provider:
            try:
                self.provider.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting provider on shutdown: {e}")

        # 2. Release authoritative OS process lock and DB heartbeat
        try:
            self.process_lock.release()
        except Exception as e:
            logger.warning(f"Error releasing process lock on shutdown: {e}")

        # 3. Restore signal handlers
        self.signals.restore_handlers()

        # 4. Record audit event
        self._log_audit_event("RUNNER_STOPPED", status="STOPPED", result=f"Runner cleanly stopped for Campaign {self.campaign_id}")

        self.lifecycle.transition_to(RunnerLifecycleState.STOPPED, "Shutdown complete")
        logger.info(f"Production runner stopped cleanly for Campaign {self.campaign_id}.")

    def _log_audit_event(
        self,
        event_type: str,
        status: str = "ACTIVE",
        result: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """Helper to write runner lifecycle events to AuditLog."""
        try:
            log = AuditLog(
                event_type=event_type,
                campaign_id=self.campaign_id,
                status=status,
                result=result,
                error_message=error
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to record audit log for {event_type}: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass
