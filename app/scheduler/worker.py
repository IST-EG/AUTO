"""
Queue Worker Orchestrator.

Orchestrates message processing from the persistent queue:
- Checks EmergencyStop at safe cancellation points
- Evaluates FrequencyLimitService and RateLimiter
- Delegates outbound sending exclusively via MessageProvider interface
- Enforces CircuitBreaker error thresholds
- Updates persistent BatchManager progression
- Tracks retry scheduling via RetryManager
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact

from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState
from app.providers.base import MessageProvider, ProviderMessage, SendResult
from app.limiter.rate_limiter import RateLimiter
from app.limiter.frequency_service import FrequencyLimitService
from app.scheduler.circuit_breaker import CircuitBreaker
from app.scheduler.emergency_stop import EmergencyStop, EmergencyStopTriggered
from app.scheduler.batch_manager import BatchManager
from app.scheduler.retry_manager import RetryManager


class QueueWorker:
    """
    Worker executing queue items through the abstracted messaging pipeline.
    """

    def __init__(
        self,
        db: Session,
        provider: MessageProvider,
        worker_id: Optional[str] = None,
        apply_pacing_delay: bool = False
    ):
        self.db = db
        self.provider = provider
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.apply_pacing_delay = apply_pacing_delay

        # Associated services
        self.queue_service = PersistentQueueService(db)
        self.rate_limiter = RateLimiter(db)
        self.frequency_service = FrequencyLimitService(db)
        self.circuit_breaker = CircuitBreaker(db)
        self.emergency_stop = EmergencyStop(db)
        self.batch_manager = BatchManager(db)
        self.retry_manager = RetryManager()

    def process_next_message(self, campaign_id: Optional[int] = None, batch_id: Optional[int] = None) -> Optional[Message]:
        """
        Pulls and processes the next eligible queue message.
        Follows strict safe cancellation points and error handling.
        Returns the processed Message or None if queue is idle or worker paused.
        """
        # Safe cancellation point 1: Emergency stop check before claiming
        if self.emergency_stop.is_active():
            return None

        # Campaign check
        campaign = None
        if campaign_id is not None:
            campaign = self.db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if not campaign or campaign.status != "RUNNING":
                return None

        # Atomic claim with persistent lease
        message = self.queue_service.claim_next_message(
            worker_id=self.worker_id,
            campaign_id=campaign_id,
            batch_id=batch_id
        )

        if not message:
            return None

        # Load relations if not attached
        if not campaign:
            campaign = self.db.query(Campaign).filter(Campaign.id == message.campaign_id).first()

        contact = self.db.query(Contact).filter(Contact.id == message.contact_id).first()

        # Safe cancellation point 2: Check after claiming but before provider dispatch
        if self.emergency_stop.is_active():
            # Release message cleanly back to QUEUED
            message.status = QueueState.QUEUED
            message.locked_at = None
            message.locked_by = None
            self.db.commit()
            return None

        # Frequency limit evaluation
        if contact and campaign:
            freq_result = self.frequency_service.check_frequency(contact, campaign)
            if not freq_result.eligible:
                skipped_msg = self.queue_service.mark_skipped(
                    message.id,
                    self.worker_id,
                    reason=f"Frequency limit: {freq_result.reason}"
                )
                if message.batch_id:
                    self.batch_manager.record_item_result(message.batch_id, success=False, skipped=True)
                return skipped_msg

        # Rate limit daily cap check
        if campaign:
            quota_available, _, _ = self.rate_limiter.check_daily_quota(campaign)
            if not quota_available:
                # Quota reached for today; release lease back to QUEUED and exit
                message.status = QueueState.QUEUED
                message.locked_at = None
                message.locked_by = None
                self.db.commit()
                return None

        # Global daily quota check
        global_available, _, _ = self.rate_limiter.check_global_daily_quota()
        if not global_available:
            message.status = QueueState.QUEUED
            message.locked_at = None
            message.locked_by = None
            self.db.commit()
            return None

        # Dispatch via MessageProvider interface
        provider_msg = ProviderMessage(
            message_id=message.id,
            idempotency_key=message.idempotency_key,
            recipient_phone=contact.phone_e164 if contact else "",
            content=message.rendered_content,
            metadata={"campaign_id": campaign.id if campaign else None, "contact_id": contact.id if contact else None}
        )

        send_result = self.provider.send_message(provider_msg)

        now = datetime.now(timezone.utc)
        if send_result.success:
            # Mark SENT
            self.queue_service.mark_sent(message.id, self.worker_id)
            if campaign:
                self.circuit_breaker.record_success(campaign.id)

            if message.batch_id:
                self.batch_manager.record_item_result(message.batch_id, success=True)

            # Update Contact last contacted stats
            if contact:
                contact.last_contacted_at = now
                contact.messages_sent_count = (contact.messages_sent_count or 0) + 1

            # Update CampaignContact if linked
            if message.campaign_contact_id:
                cc = self.db.query(CampaignContact).filter(CampaignContact.id == message.campaign_contact_id).first()
                if cc:
                    cc.status = "SENT"
                    cc.sent_at = now

            self.db.commit()
        else:
            # Failure handling
            error_text = send_result.error_message or "Unknown delivery failure"
            should_retry, _ = self.retry_manager.should_retry(
                is_temporary=send_result.is_temporary_error,
                current_attempts=message.attempt_count,
                max_attempts=message.max_attempts
            )

            if should_retry:
                delay = self.retry_manager.calculate_backoff_delay(message.attempt_count)
                self.queue_service.mark_retry(message.id, self.worker_id, error_text, delay)
            else:
                if send_result.raw_response and send_result.raw_response.get("category") == "UNKNOWN_OUTCOME":
                    err_type = "UNKNOWN_OUTCOME"
                elif error_text and "[UNKNOWN_OUTCOME]" in error_text:
                    err_type = "UNKNOWN_OUTCOME"
                elif send_result.is_temporary_error:
                    err_type = "TEMPORARY_EXHAUSTED"
                else:
                    err_type = "PERMANENT"
                self.queue_service.mark_failed(message.id, self.worker_id, error_text, error_type=err_type)
                if message.batch_id:
                    self.batch_manager.record_item_result(message.batch_id, success=False)
                if message.campaign_contact_id:
                    cc = self.db.query(CampaignContact).filter(CampaignContact.id == message.campaign_contact_id).first()
                    if cc:
                        cc.status = "FAILED"

            if campaign:
                self.circuit_breaker.record_failure(campaign, error_text)

        return message
