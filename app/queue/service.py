"""
Persistent Queue Service.

Implements database-backed queue operations:
- Deterministic idempotency keying
- Atomic worker message claiming
- Lease persistence & stale lease recovery
- Strict state machine enforcement
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_, case
from sqlalchemy.exc import IntegrityError

from app.models.message import Message
from app.queue.state_machine import QueueState, QueueStateMachine
from app.queue.exceptions import (
    DuplicateMessageError,
    MessageNotFoundError,
    MessageClaimError,
    InvalidQueueStateTransitionError,
)


class PersistentQueueService:
    """
    Service managing persistent queue operations backed by the database.
    """

    DEFAULT_LEASE_DURATION_SECONDS = 120

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def generate_idempotency_key(campaign_id: int, contact_id: int, campaign_contact_id: Optional[int] = None, sequence_number: int = 1) -> str:
        """
        Generates a deterministic unique key for a message.
        Follows the approved extensible identity model: campaign_contact_id + sequence_number.
        """
        if campaign_contact_id is not None:
            return f"cc_{campaign_contact_id}_seq_{sequence_number}"
        return f"camp_{campaign_id}_contact_{contact_id}_seq_{sequence_number}"

    def enqueue_message(
        self,
        campaign_id: int,
        contact_id: int,
        rendered_content: str,
        campaign_contact_id: Optional[int] = None,
        sequence_number: int = 1,
        initial_state: str = QueueState.PENDING,
        batch_id: Optional[int] = None,
        max_attempts: int = 3,
        custom_idempotency_key: Optional[str] = None
    ) -> Message:
        """
        Inserts a new message into the persistent queue.
        Enforces database-level idempotency and validates initial state.
        """
        if initial_state not in (QueueState.PENDING, QueueState.QUEUED):
            raise InvalidQueueStateTransitionError(
                f"Initial queue state must be PENDING or QUEUED, got '{initial_state}'."
            )

        idempotency_key = custom_idempotency_key or self.generate_idempotency_key(
            campaign_id=campaign_id,
            contact_id=contact_id,
            campaign_contact_id=campaign_contact_id,
            sequence_number=sequence_number
        )

        now = datetime.now(timezone.utc)
        message = Message(
            campaign_id=campaign_id,
            contact_id=contact_id,
            campaign_contact_id=campaign_contact_id,
            batch_id=batch_id,
            sequence_number=sequence_number,
            idempotency_key=idempotency_key,
            rendered_content=rendered_content,
            status=initial_state,
            attempt_count=0,
            max_attempts=max_attempts,
            retry_count=0,
            queued_at=now,
            created_at=now,
            updated_at=now
        )

        self.db.add(message)
        try:
            self.db.commit()
            self.db.refresh(message)
        except IntegrityError:
            self.db.rollback()
            raise DuplicateMessageError(
                f"Message with idempotency_key '{idempotency_key}' already exists."
            )

        return message

    def promote_pending_to_queued(self, campaign_id: Optional[int] = None, batch_id: Optional[int] = None, limit: int = 100) -> int:
        """
        Transitions eligible PENDING messages to QUEUED state.
        Used when a campaign transitions to RUNNING or when a batch begins.
        """
        query = self.db.query(Message).filter(Message.status == QueueState.PENDING)
        if campaign_id is not None:
            query = query.filter(Message.campaign_id == campaign_id)
        if batch_id is not None:
            query = query.filter(Message.batch_id == batch_id)

        messages = query.limit(limit).all()
        promoted = 0
        now = datetime.now(timezone.utc)
        for msg in messages:
            QueueStateMachine.validate_transition(msg.status, QueueState.QUEUED, msg.id)
            msg.status = QueueState.QUEUED
            msg.updated_at = now
            promoted += 1

        if promoted > 0:
            self.db.commit()

        return promoted

    def claim_next_message(
        self,
        worker_id: str,
        campaign_id: Optional[int] = None,
        batch_id: Optional[int] = None,
        lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS
    ) -> Optional[Message]:
        """
        Atomically claims the next eligible message for a worker with a persistent lease.
        Prevents two workers from claiming the same message.
        Supports both SQLite and PostgreSQL concurrency models.
        """
        now = datetime.now(timezone.utc)

        # Base query to find the best candidate message
        candidate_query = self.db.query(Message.id).filter(
            or_(
                Message.status == QueueState.QUEUED,
                and_(
                    Message.status == QueueState.RETRY_PENDING,
                    or_(Message.next_retry_at.is_(None), Message.next_retry_at <= now)
                )
            )
        )

        if campaign_id is not None:
            candidate_query = candidate_query.filter(Message.campaign_id == campaign_id)
        if batch_id is not None:
            candidate_query = candidate_query.filter(Message.batch_id == batch_id)

        # Order by: RETRY_PENDING with earliest retry first, then oldest queued
        candidate_query = candidate_query.order_by(
            case((Message.next_retry_at.is_(None), 1), else_=0),
            Message.next_retry_at.asc(),
            Message.id.asc()
        )

        # Iterate candidates and attempt atomic claim
        candidates = candidate_query.limit(10).all()
        for (candidate_id,) in candidates:
            # Atomic update: only updates if row still in claimable status and not locked by active lease
            rows_updated = self.db.query(Message).filter(
                Message.id == candidate_id,
                or_(
                    Message.status == QueueState.QUEUED,
                    and_(
                        Message.status == QueueState.RETRY_PENDING,
                        or_(Message.next_retry_at.is_(None), Message.next_retry_at <= now)
                    )
                ),
                or_(
                    Message.locked_at.is_(None),
                    Message.locked_at < now - timedelta(seconds=lease_duration_seconds)
                )
            ).update(
                {
                    "status": QueueState.PROCESSING,
                    "locked_at": now,
                    "locked_by": worker_id,
                    "last_attempt_at": now,
                    "attempt_count": Message.attempt_count + 1,
                    "retry_count": Message.retry_count + 1,
                    "updated_at": now
                },
                synchronize_session=False
            )

            if rows_updated == 1:
                self.db.commit()
                return self.db.query(Message).filter(Message.id == candidate_id).first()
            else:
                self.db.rollback()

        return None

    def mark_sent(self, message_id: int, worker_id: str) -> Message:
        """
        Marks a message as successfully sent (Terminal state).
        Releases worker lock.
        """
        message = self.db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found.")

        QueueStateMachine.validate_transition(message.status, QueueState.SENT, message_id)

        now = datetime.now(timezone.utc)
        message.status = QueueState.SENT
        message.sent_at = now
        message.locked_at = None
        message.locked_by = None
        message.updated_at = now

        self.db.commit()
        self.db.refresh(message)
        return message

    def mark_failed(self, message_id: int, worker_id: str, error_message: str, error_type: str = "PERMANENT") -> Message:
        """
        Marks a message as permanently failed (Terminal state).
        Releases worker lock.
        """
        message = self.db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found.")

        QueueStateMachine.validate_transition(message.status, QueueState.FAILED, message_id)

        now = datetime.now(timezone.utc)
        message.status = QueueState.FAILED
        message.failed_at = now
        message.last_error = error_message
        message.error_type = error_type
        message.locked_at = None
        message.locked_by = None
        message.updated_at = now

        self.db.commit()
        self.db.refresh(message)
        return message

    def mark_retry(self, message_id: int, worker_id: str, error_message: str, delay_seconds: float) -> Message:
        """
        Marks a message for retry following temporary failure.
        Schedules next_retry_at and transitions to RETRY_PENDING.
        Releases worker lease.
        """
        message = self.db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found.")

        now = datetime.now(timezone.utc)

        # Check if attempts exceeded
        if message.attempt_count >= message.max_attempts:
            return self.mark_failed(
                message_id=message_id,
                worker_id=worker_id,
                error_message=f"Exhausted max attempts ({message.max_attempts}): {error_message}",
                error_type="TEMPORARY_EXHAUSTED"
            )

        QueueStateMachine.validate_transition(message.status, QueueState.RETRY_PENDING, message_id)

        message.status = QueueState.RETRY_PENDING
        message.last_error = error_message
        message.error_type = "TEMPORARY"
        message.next_retry_at = now + timedelta(seconds=delay_seconds)
        message.locked_at = None
        message.locked_by = None
        message.updated_at = now

        self.db.commit()
        self.db.refresh(message)
        return message

    def mark_skipped(self, message_id: int, worker_id: str, reason: str) -> Message:
        """
        Marks a message as skipped (Terminal state).
        """
        message = self.db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found.")

        QueueStateMachine.validate_transition(message.status, QueueState.SKIPPED, message_id)

        now = datetime.now(timezone.utc)
        message.status = QueueState.SKIPPED
        message.last_error = reason
        message.locked_at = None
        message.locked_by = None
        message.updated_at = now

        self.db.commit()
        self.db.refresh(message)
        return message

    def cancel_message(self, message_id: int) -> Message:
        """
        Cancels a message (Terminal state).
        """
        message = self.db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found.")

        QueueStateMachine.validate_transition(message.status, QueueState.CANCELLED, message_id)

        now = datetime.now(timezone.utc)
        message.status = QueueState.CANCELLED
        message.locked_at = None
        message.locked_by = None
        message.updated_at = now

        self.db.commit()
        self.db.refresh(message)
        return message

    def recover_stale_leases(self, lease_timeout_seconds: int = DEFAULT_LEASE_DURATION_SECONDS) -> int:
        """
        Identifies messages stuck in PROCESSING whose worker lease expired.
        Resets them to RETRY_PENDING or FAILED if max_attempts reached.
        Safe to run upon application startup and periodically.
        """
        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(seconds=lease_timeout_seconds)

        stale_messages = self.db.query(Message).filter(
            Message.status == QueueState.PROCESSING,
            Message.locked_at < stale_threshold
        ).all()

        recovered_count = 0
        for msg in stale_messages:
            if msg.attempt_count >= msg.max_attempts:
                msg.status = QueueState.FAILED
                msg.failed_at = now
                msg.error_type = "STALE_LEASE_EXHAUSTED"
                msg.last_error = f"Worker lease expired after {lease_timeout_seconds}s and attempts exhausted."
            else:
                msg.status = QueueState.RETRY_PENDING
                msg.next_retry_at = now  # Eligible immediately
                msg.error_type = "STALE_LEASE_RECOVERED"
                msg.last_error = f"Recovered from stale lease by worker '{msg.locked_by}'."

            msg.locked_at = None
            msg.locked_by = None
            msg.updated_at = now
            recovered_count += 1

        if recovered_count > 0:
            self.db.commit()

        return recovered_count

    def get_queue_metrics(self, campaign_id: Optional[int] = None) -> Dict[str, int]:
        """
        Returns count of messages in each queue status.
        """
        query = self.db.query(Message.status, func.count(Message.id))
        if campaign_id is not None:
            query = query.filter(Message.campaign_id == campaign_id)

        results = query.group_by(Message.status).all()

        metrics = {state: 0 for state in [
            QueueState.PENDING, QueueState.QUEUED, QueueState.PROCESSING,
            QueueState.SENT, QueueState.FAILED, QueueState.RETRY_PENDING,
            QueueState.CANCELLED, QueueState.SKIPPED
        ]}
        for status, count in results:
            if status in metrics:
                metrics[status] = count

        return metrics
