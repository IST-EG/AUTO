"""
Web Domain Service for Queue and Message Operations.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_, and_

from app.models.message import Message
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.audit_log import AuditLog
from app.models.user import User
from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState, QueueStateMachine
from app.scheduler.emergency_stop import EmergencyStop
from app.web.services.contact_service import mask_phone_number
from app.web.schemas.queue import (
    QueueMessageItemDTO,
    QueueListResponse,
    QueueAuditLogItemDTO,
    QueueMessageDetailDTO,
    QueueStatsDTO,
    UnknownOutcomeResolveRequest,
    QueueReconcileResponse,
)


class WebQueueService:
    """
    Control plane service managing operational queue inspection, reconciliation,
    cancellation, and UNKNOWN_OUTCOME manual resolution.
    """

    @staticmethod
    def should_mask_phone(user: User) -> bool:
        """Role-based privacy: VIEWER and OPERATOR receive masked phone numbers."""
        return user.role not in ("ADMIN", "OWNER")

    @classmethod
    def list_messages(
        cls,
        db: Session,
        user: User,
        page: int = 1,
        page_size: int = 20,
        campaign_id: Optional[int] = None,
        status: Optional[str] = None,
        contact_id: Optional[int] = None,
        contact_query: Optional[str] = None,
        retry_filter: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> QueueListResponse:
        """
        Lists queued and historical message records with server-side pagination,
        filtering, and role-based phone privacy masking.
        """
        page = max(1, page)
        page_size = min(max(1, page_size), 100)

        query = db.query(Message).options(
            joinedload(Message.campaign),
            joinedload(Message.contact)
        )

        # 1. Campaign Filter
        if campaign_id:
            query = query.filter(Message.campaign_id == campaign_id)

        # 2. Status Filter
        if status and status.upper() != "ALL":
            status_upper = status.upper()
            if status_upper == "UNKNOWN_OUTCOME":
                query = query.filter(
                    Message.status == "FAILED",
                    Message.error_type == "UNKNOWN_OUTCOME"
                )
            else:
                query = query.filter(Message.status == status_upper)

        # 3. Contact Filter
        if contact_id:
            query = query.filter(Message.contact_id == contact_id)

        # 4. Contact Text Query (Search name or phone)
        if contact_query and contact_query.strip():
            cq = f"%{contact_query.strip()}%"
            query = query.join(Message.contact).filter(
                or_(
                    Contact.name.ilike(cq),
                    Contact.phone_e164.ilike(cq)
                )
            )

        # 5. Retry Filter
        if retry_filter and retry_filter.upper() != "ALL":
            rf = retry_filter.upper()
            if rf == "HAS_RETRY":
                query = query.filter(or_(Message.attempt_count > 1, Message.retry_count > 0))
            elif rf == "NO_RETRY":
                query = query.filter(Message.attempt_count <= 1, Message.retry_count == 0)
            elif rf == "RETRY_PENDING":
                query = query.filter(Message.status == QueueState.RETRY_PENDING)

        # 6. Date Range Bounds
        if date_from:
            query = query.filter(Message.created_at >= date_from)
        if date_to:
            query = query.filter(Message.created_at <= date_to)

        total = query.count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        offset = (page - 1) * page_size

        messages = query.order_by(Message.id.desc()).offset(offset).limit(page_size).all()

        mask_phone = cls.should_mask_phone(user)
        items: List[QueueMessageItemDTO] = []
        for msg in messages:
            contact = msg.contact
            phone = contact.phone_e164 if contact else None
            if phone and mask_phone:
                phone = mask_phone_number(phone)

            contact_name = contact.name if contact else None

            campaign_name = msg.campaign.name if msg.campaign else None

            is_uo = (msg.status == "FAILED" and msg.error_type == "UNKNOWN_OUTCOME")

            items.append(QueueMessageItemDTO(
                id=msg.id,
                campaign_id=msg.campaign_id,
                campaign_name=campaign_name,
                contact_id=msg.contact_id,
                contact_name=contact_name,
                contact_phone=phone,
                status=msg.status,
                is_unknown_outcome=is_uo,
                attempt_count=msg.attempt_count,
                max_attempts=msg.max_attempts,
                retry_count=msg.retry_count,
                next_retry_at=msg.next_retry_at,
                last_attempt_at=msg.last_attempt_at,
                error_type=msg.error_type,
                last_error=msg.last_error,
                queued_at=msg.queued_at,
                sent_at=msg.sent_at,
                failed_at=msg.failed_at,
                created_at=msg.created_at,
                updated_at=msg.updated_at,
            ))

        return QueueListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )

    @classmethod
    def get_message_detail(cls, db: Session, user: User, message_id: int) -> QueueMessageDetailDTO:
        """
        Retrieves complete inspection details for a single message record.
        Includes lease status, retry info, rendered content, and recent audit logs.
        """
        msg = (
            db.query(Message)
            .options(
                joinedload(Message.campaign),
                joinedload(Message.contact),
                joinedload(Message.campaign_contact)
            )
            .filter(Message.id == message_id)
            .first()
        )
        if not msg:
            raise HTTPException(status_code=404, detail=f"Message {message_id} not found")

        contact = msg.contact
        phone = contact.phone_e164 if contact else None
        if phone and cls.should_mask_phone(user):
            phone = mask_phone_number(phone)

        contact_name = contact.name if contact else None

        campaign_name = msg.campaign.name if msg.campaign else None

        is_uo = (msg.status == "FAILED" and msg.error_type == "UNKNOWN_OUTCOME")

        # Stale lease calculation
        is_stale = False
        if msg.status == QueueState.PROCESSING and msg.locked_at:
            is_stale = msg.locked_at < (datetime.now(timezone.utc) - timedelta(seconds=PersistentQueueService.DEFAULT_LEASE_DURATION_SECONDS))

        can_cancel = QueueStateMachine.can_transition(msg.status, QueueState.CANCELLED)
        can_resolve_unknown = is_uo

        # Query recent audit logs for this message
        raw_logs = (
            db.query(AuditLog)
            .filter(AuditLog.message_id == message_id)
            .order_by(AuditLog.id.desc())
            .limit(20)
            .all()
        )
        audit_logs = [
            QueueAuditLogItemDTO(
                id=log.id,
                event_type=log.event_type,
                status=log.status,
                actor=log.actor,
                created_at=log.created_at,
                result=log.result,
                error_message=log.error_message
            )
            for log in raw_logs
        ]

        return QueueMessageDetailDTO(
            id=msg.id,
            campaign_id=msg.campaign_id,
            campaign_name=campaign_name,
            contact_id=msg.contact_id,
            contact_name=contact_name,
            contact_phone=phone,
            campaign_contact_id=msg.campaign_contact_id,
            batch_id=msg.batch_id,
            sequence_number=msg.sequence_number,
            idempotency_key=msg.idempotency_key,
            rendered_content=msg.rendered_content,
            status=msg.status,
            is_unknown_outcome=is_uo,
            attempt_count=msg.attempt_count,
            max_attempts=msg.max_attempts,
            retry_count=msg.retry_count,
            next_retry_at=msg.next_retry_at,
            last_attempt_at=msg.last_attempt_at,
            error_type=msg.error_type,
            last_error=msg.last_error,
            locked_at=msg.locked_at,
            locked_by=msg.locked_by,
            is_lease_stale=is_stale,
            can_cancel=can_cancel,
            can_resolve_unknown=can_resolve_unknown,
            queued_at=msg.queued_at,
            sent_at=msg.sent_at,
            failed_at=msg.failed_at,
            created_at=msg.created_at,
            updated_at=msg.updated_at,
            audit_logs=audit_logs,
        )

    @classmethod
    def get_queue_stats(cls, db: Session) -> QueueStatsDTO:
        """
        Aggregates operational queue status counts, stale lease counter,
        Emergency Stop / Circuit Breaker status, and locked Confirmed Send Rate.
        """
        # Status and error_type breakdown in a single grouped query
        msg_rows = (
            db.query(Message.status, Message.error_type, func.count(Message.id))
            .group_by(Message.status, Message.error_type)
            .all()
        )

        counts = {
            QueueState.PENDING: 0,
            QueueState.QUEUED: 0,
            QueueState.PROCESSING: 0,
            QueueState.SENT: 0,
            QueueState.RETRY_PENDING: 0,
            QueueState.FAILED: 0,
            QueueState.SKIPPED: 0,
            QueueState.CANCELLED: 0,
        }
        unknown_outcome_count = 0
        total = 0

        for status_val, error_type_val, count_val in msg_rows:
            st = (status_val or "").upper()
            et = (error_type_val or "").upper()
            total += count_val
            if st in counts:
                counts[st] += count_val
            if st == "FAILED" and et == "UNKNOWN_OUTCOME":
                unknown_outcome_count += count_val

        # Stale leases count
        stale_threshold = datetime.now(timezone.utc) - timedelta(seconds=PersistentQueueService.DEFAULT_LEASE_DURATION_SECONDS)
        stale_leases = (
            db.query(func.count(Message.id))
            .filter(Message.status == QueueState.PROCESSING, Message.locked_at < stale_threshold)
            .scalar() or 0
        )

        # Locked Confirmed Send Rate calculation:
        # confirmed_send_rate = confirmed_sends / (confirmed_sends + failed + unknown_outcome) * 100
        # Note: counts[FAILED] already includes unknown_outcome_count.
        # So terminal_denominator = counts[SENT] + counts[FAILED]
        sent_count = counts[QueueState.SENT]
        failed_count = counts[QueueState.FAILED]
        terminal_denominator = sent_count + failed_count
        confirmed_send_rate = 0.0
        if terminal_denominator > 0:
            confirmed_send_rate = round((sent_count / terminal_denominator) * 100.0, 2)

        emergency_stop_active = EmergencyStop(db).is_active()

        # Check Circuit Breaker status across paused campaigns
        tripped_campaigns = db.query(Campaign.id).filter(Campaign.status == "PAUSED").count()
        circuit_breaker_open = tripped_campaigns > 0

        return QueueStatsDTO(
            total=total,
            pending=counts[QueueState.PENDING],
            queued=counts[QueueState.QUEUED],
            processing=counts[QueueState.PROCESSING],
            sent=sent_count,
            retry_pending=counts[QueueState.RETRY_PENDING],
            failed=failed_count,
            unknown_outcome=unknown_outcome_count,
            cancelled=counts[QueueState.CANCELLED],
            skipped=counts[QueueState.SKIPPED],
            stale_leases=stale_leases,
            confirmed_send_rate=confirmed_send_rate,
            emergency_stop_active=emergency_stop_active,
            circuit_breaker_open=circuit_breaker_open,
        )

    @classmethod
    def reconcile_queue(cls, db: Session, user: User) -> QueueReconcileResponse:
        """
        Reconciles stale worker leases back to claimable queue state using
        authoritative Phase 3 PersistentQueueService recovery logic.
        """
        queue_service = PersistentQueueService(db)
        recovered_count = queue_service.recover_stale_leases()

        uo_count = (
            db.query(func.count(Message.id))
            .filter(Message.status == "FAILED", Message.error_type == "UNKNOWN_OUTCOME")
            .scalar() or 0
        )

        now = datetime.now(timezone.utc)
        audit_payload = {
            "operator": user.username,
            "recovered_leases": recovered_count,
            "pending_unknown_outcomes": uo_count,
            "timestamp": now.isoformat(),
        }
        audit = AuditLog(
            event_type="QUEUE_RECONCILE_REQUESTED",
            status="SUCCESS",
            result=json.dumps(audit_payload)
        )
        db.add(audit)
        db.commit()

        return QueueReconcileResponse(
            recovered_leases=recovered_count,
            pending_unknown_outcomes=uo_count,
            reconciled_at=now
        )

    @classmethod
    def cancel_message(cls, db: Session, user: User, message_id: int) -> QueueMessageDetailDTO:
        """
        Cancels a message from non-terminal states (PENDING, QUEUED, RETRY_PENDING).
        Uses authoritative Phase 3 cancellation and records audit trail.
        """
        message = db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise HTTPException(status_code=404, detail=f"Message {message_id} not found")

        if not QueueStateMachine.can_transition(message.status, QueueState.CANCELLED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel message {message_id} in state '{message.status}'. Cancellation is only allowed from non-terminal states."
            )

        previous_state = message.status
        queue_service = PersistentQueueService(db)
        cancelled_msg = queue_service.cancel_message(message_id)

        now = datetime.now(timezone.utc)
        audit = AuditLog(
            event_type="QUEUE_CANCEL_REQUESTED",
            campaign_id=cancelled_msg.campaign_id,
            contact_id=cancelled_msg.contact_id,
            message_id=cancelled_msg.id,
            status=QueueState.CANCELLED,
            result=json.dumps({
                "operator": user.username,
                "previous_state": previous_state,
                "new_state": QueueState.CANCELLED,
                "timestamp": now.isoformat(),
            })
        )
        db.add(audit)
        db.commit()

        return cls.get_message_detail(db, user, message_id)

    @classmethod
    def resolve_unknown_outcome(
        cls,
        db: Session,
        user: User,
        message_id: int,
        request: UnknownOutcomeResolveRequest
    ) -> QueueMessageDetailDTO:
        """
        Strictly audited manual resolution for UNKNOWN_OUTCOME messages.
        Requires external verification, non-empty reason, and exact confirmation phrase 'CONFIRM-NOT-DELIVERED'.
        Follows Phase 5 manual reconciliation semantics.
        """
        message = db.query(Message).filter(Message.id == message_id).first()
        if not message:
            raise HTTPException(status_code=404, detail=f"Message {message_id} not found")

        if message.status != "FAILED" or message.error_type != "UNKNOWN_OUTCOME":
            raise HTTPException(
                status_code=400,
                detail=f"Message {message_id} is not in UNKNOWN_OUTCOME status. Manual resolution is strictly reserved for UNKNOWN_OUTCOME messages."
            )

        if not request.reason or not request.reason.strip():
            raise HTTPException(
                status_code=422,
                detail="An explicit explanation reason is required for manual resolution."
            )

        required_confirmation = "CONFIRM-NOT-DELIVERED"
        if request.confirmation_phrase != required_confirmation:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid confirmation phrase. Expected '{required_confirmation}', got '{request.confirmation_phrase}'."
            )

        # Transition message back to QUEUED for dispatch
        now = datetime.now(timezone.utc)
        previous_state = message.status
        message.status = QueueState.QUEUED
        message.locked_at = None
        message.locked_by = None
        message.updated_at = now
        message.last_error = f"[MANUAL_OVERRIDE] {request.reason.strip()}"

        audit_payload = {
            "message_id": message.id,
            "campaign_id": message.campaign_id,
            "contact_id": message.contact_id,
            "previous_state": previous_state,
            "new_state": QueueState.QUEUED,
            "timestamp": now.isoformat(),
            "operator_identity": user.username,
            "explicit_override_reason": request.reason.strip(),
            "confirmation_phrase": required_confirmation,
            "manual_verification_confirmed": True,
            "override_action": "UNKNOWN_OUTCOME_RESOLUTION_REQUESTED",
        }

        audit = AuditLog(
            event_type="UNKNOWN_OUTCOME_RESOLUTION_REQUESTED",
            campaign_id=message.campaign_id,
            contact_id=message.contact_id,
            message_id=message.id,
            status=QueueState.QUEUED,
            result=json.dumps(audit_payload),
            error_message=f"Operator '{user.username}' confirmed message was not delivered externally."
        )
        db.add(audit)
        db.commit()

        return cls.get_message_detail(db, user, message_id)
