"""
Pydantic schemas and DTOs for Queue and Message Operations.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class QueueMessageItemDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    campaign_name: Optional[str] = None
    contact_id: int
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    status: str
    is_unknown_outcome: bool = False
    attempt_count: int = 0
    max_attempts: int = 3
    retry_count: int = 0
    next_retry_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    error_type: Optional[str] = None
    last_error: Optional[str] = None
    queued_at: datetime
    sent_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class QueueListResponse(BaseModel):
    items: List[QueueMessageItemDTO]
    total: int
    page: int
    page_size: int
    total_pages: int


class QueueAuditLogItemDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    status: Optional[str] = None
    actor: Optional[str] = None
    created_at: datetime
    result: Optional[str] = None
    error_message: Optional[str] = None


class QueueMessageDetailDTO(QueueMessageItemDTO):
    campaign_contact_id: Optional[int] = None
    batch_id: Optional[int] = None
    sequence_number: int = 1
    idempotency_key: str
    rendered_content: str
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    is_lease_stale: bool = False
    can_cancel: bool = False
    can_resolve_unknown: bool = False
    audit_logs: List[QueueAuditLogItemDTO] = []


class QueueStatsDTO(BaseModel):
    total: int = 0
    pending: int = 0
    queued: int = 0
    processing: int = 0
    sent: int = 0
    retry_pending: int = 0
    failed: int = 0
    unknown_outcome: int = 0
    cancelled: int = 0
    skipped: int = 0
    stale_leases: int = 0
    confirmed_send_rate: float = 0.0
    emergency_stop_active: bool = False
    circuit_breaker_open: bool = False


class UnknownOutcomeResolveRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Explicit reason for manual resolution after external verification")
    confirmation_phrase: str = Field(..., description="Must be exactly 'CONFIRM-NOT-DELIVERED'")


class QueueReconcileResponse(BaseModel):
    recovered_leases: int
    pending_unknown_outcomes: int
    reconciled_at: datetime
