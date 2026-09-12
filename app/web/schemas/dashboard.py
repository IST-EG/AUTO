"""
Pydantic schemas and DTOs for the Web Control Center Dashboard.

Encapsulates the aggregated operational snapshot, active campaign status,
queue backlog metrics, runner telemetry, and operational alert models.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class SystemHealthDTO(BaseModel):
    state: str = Field(..., description="Operational state: HEALTHY, DEGRADED, UNHEALTHY, STOPPED")
    reasons: List[str] = Field(default_factory=list, description="Explanatory reasons for current health state")
    details: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic telemetry breakdown")


class DatabaseStatusDTO(BaseModel):
    connected: bool = Field(..., description="Whether database connection check succeeded")
    status: str = Field(..., description="CONNECTED or UNAVAILABLE")


class WhatsAppProviderStatusDTO(BaseModel):
    state: str = Field(..., description="Provider session state (CONNECTED, AUTHENTICATING, DISCONNECTED, etc.)")
    profile_exists: bool = Field(..., description="Whether persistent browser profile directory exists")
    has_profile_data: bool = Field(..., description="Whether cached profile contains session cookies/data")
    disclaimer: str = Field(
        default="SEND_CONFIRMED represents WhatsApp Web UI send confirmation only, not delivery or read receipts.",
        description="Mandatory semantic clarification"
    )


class EmergencyStopStatusDTO(BaseModel):
    is_active: bool = Field(..., description="Whether system killswitch is currently engaged")
    status: str = Field(..., description="ACTIVE (HALTED) or INACTIVE (OPERATIONAL)")
    reason: Optional[str] = Field(None, description="Operator justification for emergency stop")
    activated_at: Optional[str] = Field(None, description="ISO timestamp when emergency stop was triggered")


class CircuitBreakerStatusDTO(BaseModel):
    status: str = Field(..., description="CLOSED (Normal) or OPEN (Tripped)")
    consecutive_errors: int = Field(0, description="Current consecutive errors count")
    threshold: int = Field(5, description="Consecutive failure limit before tripping")
    tripped_campaigns: List[int] = Field(default_factory=list, description="IDs of campaigns paused by circuit breaker")


class ActiveCampaignDTO(BaseModel):
    id: int = Field(..., description="Campaign identifier")
    name: str = Field(..., description="Campaign name")
    status: str = Field(..., description="Campaign status (RUNNING, SCHEDULED, PAUSED, etc.)")
    total_contacts: int = Field(0, description="Total contacts in campaign target list")
    eligible: int = Field(0, description="Eligible contacts approved for outreach")
    queued: int = Field(0, description="Messages awaiting worker claim")
    send_confirmed: int = Field(0, description="Messages confirmed sent via WhatsApp Web UI checkmark")
    failed: int = Field(0, description="Messages with permanent failure (no retries left)")
    unknown_outcome: int = Field(0, description="Dispatches clicked but unconfirmed; requires physical verification")
    retry_pending: int = Field(0, description="Messages awaiting exponential backoff retry")
    remaining: int = Field(0, description="Uncompleted messages (queued + processing + retry_pending)")
    completion_percentage: float = Field(0.0, description="Progress through total contact audience")
    confirmed_send_rate: float = Field(
        0.0,
        description="LOCKED FORMULA: confirmed_sends / (confirmed_sends + failed + unknown_outcome) * 100"
    )
    disclaimer: str = Field(
        default="SEND_CONFIRMED represents WhatsApp Web UI send confirmation only. UNKNOWN_OUTCOME requires manual physical verification.",
        description="Mandatory semantic clarification"
    )


class QueueSummaryDTO(BaseModel):
    queued: int = Field(0, description="Messages waiting in QUEUED state")
    processing: int = Field(0, description="Messages claimed by worker in PROCESSING state")
    retry_pending: int = Field(0, description="Messages in RETRY_PENDING state")
    confirmed_sends: int = Field(0, description="Messages in SENT state")
    failed: int = Field(0, description="Messages in FAILED state (excluding UNKNOWN_OUTCOME)")
    unknown_outcome: int = Field(0, description="Messages in FAILED state with error_type UNKNOWN_OUTCOME")
    stale_leases: int = Field(0, description="Processing messages exceeding 300s lease timeout")
    throughput_1h: int = Field(0, description="Confirmed sends in last 1 hour")
    throughput_6h: int = Field(0, description="Confirmed sends in last 6 hours")
    throughput_24h: int = Field(0, description="Confirmed sends in last 24 hours")


class RunnerSummaryDTO(BaseModel):
    is_running: bool = Field(..., description="Whether runner process PID is actively running")
    state: str = Field(..., description="Runner state: RUNNING, STOPPED, DEGRADED, UNHEALTHY")
    pid: Optional[int] = Field(None, description="Operating system Process ID")
    worker_id: Optional[str] = Field(None, description="Unique worker instance identifier")
    campaign_id: Optional[int] = Field(None, description="ID of campaign currently being processed")
    started_at: Optional[str] = Field(None, description="ISO timestamp when runner started")
    uptime_seconds: float = Field(0.0, description="Elapsed runtime in seconds")
    heartbeat_age_seconds: Optional[float] = Field(None, description="Seconds since last runner heartbeat")
    is_stale: bool = Field(False, description="True if heartbeat age exceeds 60 seconds")
    dispatches_completed: int = Field(0, description="Dispatches handled during current runner execution")
    lock_authoritative: bool = Field(True, description="Enforced by OS file lock data/runner.lock")


class AlertDTO(BaseModel):
    id: str = Field(..., description="Unique alert identifier")
    severity: str = Field(..., description="Severity level: CRITICAL, HIGH, MEDIUM, INFO")
    title: str = Field(..., description="Short alert headline")
    message: str = Field(..., description="Detailed explanation and recommended action")
    timestamp: str = Field(..., description="ISO timestamp of alert generation")


class DashboardSnapshotDTO(BaseModel):
    timestamp: str = Field(..., description="ISO timestamp of snapshot generation")
    system_health: SystemHealthDTO
    database: DatabaseStatusDTO
    whatsapp: WhatsAppProviderStatusDTO
    emergency_stop: EmergencyStopStatusDTO
    circuit_breaker: CircuitBreakerStatusDTO
    active_campaign: Optional[ActiveCampaignDTO] = Field(None, description="Currently active campaign, or None")
    queue: QueueSummaryDTO
    runner: RunnerSummaryDTO
    alerts: List[AlertDTO] = Field(default_factory=list, description="Active operational warnings and alerts")
