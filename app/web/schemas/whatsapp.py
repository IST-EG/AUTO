"""
Pydantic schemas for WhatsApp Operations & Session Control Center.

Enforces:
- Strict path sanitization (zero Worker host filesystem paths).
- Observable command lifecycle DTOs (REQUESTED -> CLAIMED -> EXECUTING -> COMPLETED/FAILED).
- Clean separation of desired state, worker coupling, and provider readiness.
"""

from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


class WhatsAppSessionStateEnum(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTED = "CONNECTED"
    SESSION_LOST = "SESSION_LOST"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class WhatsAppHealthEnum(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    STOPPED = "STOPPED"


class ProfileStorageState(str, Enum):
    PRESENT = "PRESENT"
    EMPTY = "EMPTY"
    MISSING = "MISSING"


class WhatsAppCommandStatus(str, Enum):
    REQUESTED = "REQUESTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WhatsAppActionType(str, Enum):
    HEALTH_CHECK = "HEALTH_CHECK"
    RECONNECT = "RECONNECT"
    DISCONNECT = "DISCONNECT"
    LOGOUT = "LOGOUT"


class WhatsAppCommandDTO(BaseModel):
    request_id: str = Field(..., description="Unique command tracking identifier")
    action: str = Field(..., description="Action type: HEALTH_CHECK, RECONNECT, DISCONNECT, LOGOUT")
    status: str = Field(..., description="Lifecycle status: REQUESTED, CLAIMED, EXECUTING, COMPLETED, FAILED")
    version: int = Field(1, description="Monotonic version for atomic CAS claiming")
    requested_by: str = Field(..., description="Username of initiating operator")
    requested_at: str = Field(..., description="ISO 8601 UTC timestamp of request submission")
    claimed_by: Optional[str] = Field(None, description="Worker daemon identifier that claimed the lease")
    claimed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp when worker claimed the command")
    lease_expires_at: Optional[str] = Field(None, description="ISO 8601 UTC lease expiration timestamp (60s)")
    executed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp when execution began")
    completed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp when command completed or failed")
    result: Optional[str] = Field(None, description="Execution outcome details upon success")
    error_message: Optional[str] = Field(None, description="Sanitized failure diagnostic message if failed")


class WhatsAppStatusDTO(BaseModel):
    state: str = Field(..., description="Current WhatsApp session state")
    health_state: str = Field(..., description="System-level health evaluation (HEALTHY, DEGRADED, UNHEALTHY, STOPPED)")
    is_runner_active: bool = Field(..., description="True if a worker daemon process is actively running")
    runner_pid: Optional[int] = Field(None, description="Operating system PID of active runner daemon")
    runner_worker_id: Optional[str] = Field(None, description="Worker identifier")
    runner_campaign_id: Optional[int] = Field(None, description="Campaign ID being processed by active runner")
    profile_present: bool = Field(..., description="True if persistent session profile directory exists on Worker")
    profile_storage_state: str = Field(..., description="Storage status: PRESENT, EMPTY, MISSING")
    profile_size_bytes: Optional[int] = Field(None, description="Sanitized storage footprint in bytes")
    profile_writable: bool = Field(True, description="True if storage directory is writable")
    headless: bool = Field(False, description="Whether worker is configured in headless browser mode")
    browser_timeout_seconds: int = Field(30, description="Configured element wait timeout")
    qr_timeout_seconds: int = Field(120, description="Configured QR code authentication timeout")
    last_health_check_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of last health probe")
    last_health_check_age_seconds: Optional[float] = Field(None, description="Elapsed seconds since last health probe")
    last_state_transition_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of last state transition")
    active_command: Optional[WhatsAppCommandDTO] = Field(None, description="Currently active/in-flight command")
    last_completed_command: Optional[WhatsAppCommandDTO] = Field(None, description="Most recently concluded command")
    diagnostic_snippet: Optional[str] = Field(None, description="Sanitized diagnostic snippet (e.g. page title/status)")
    disclaimer: str = Field(
        default="SEND_CONFIRMED represents WhatsApp Web UI send confirmation only, not delivery or read receipts.",
        description="Mandatory semantic disclaimer"
    )


class WhatsAppCheckDetailDTO(BaseModel):
    name: str = Field(..., description="Diagnostic check title")
    passed: bool = Field(..., description="Check status pass/fail")
    message: str = Field(..., description="Sanitized diagnostic message")
    details: Dict[str, Any] = Field(default_factory=dict, description="Safe metadata (zero raw filesystem paths)")


class WhatsAppDiagnosticsDTO(BaseModel):
    overall_ready: bool = Field(..., description="True if all critical environmental checks passed")
    checks: List[WhatsAppCheckDetailDTO] = Field(default_factory=list, description="Detailed diagnostic inspection items")
    last_error: Optional[str] = Field(None, description="Most recent error message if degraded")
    recommendations: List[str] = Field(default_factory=list, description="Actionable runbook recommendations")


# Operational Mutation Request Models
class WhatsAppReconnectRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255, description="Operational rationale for session restart/recovery")


class WhatsAppDisconnectRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255, description="Operational rationale for controlled graceful shutdown")


class WhatsAppLogoutRequest(BaseModel):
    confirm_phrase: str = Field(..., description="Must match exactly 'CONFIRM-LOGOUT'")
    clear_cache: bool = Field(False, description="If True, instructs worker to wipe cached session profile directory")
    reason: str = Field(..., min_length=3, max_length=255, description="Operational rationale for session logout")
