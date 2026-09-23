"""
Pydantic schemas for WhatsApp Operations & Session Control Center.

Enforces:
- Strict path sanitization (zero Worker host filesystem paths).
- Observable command lifecycle DTOs (REQUESTED -> CLAIMED -> EXECUTING -> COMPLETED/FAILED).
- Clean separation of desired state, worker coupling, and provider readiness.

Phase 7.7-B Step 7 additions:
- WhatsAppWorkerIdentityDTO: stable worker instance metadata
- WhatsAppWorkerHeartbeatDTO: infrastructure health snapshot
- WorkerInfraHealthEnum: infrastructure health state enumeration
- WhatsAppStatusDTO extended with five independent health dimensions
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


class WorkerInfraHealthEnum(str, Enum):
    """Infrastructure-level health state for the Oracle worker host."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


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
    PREFLIGHT = "PREFLIGHT"


class WhatsAppCommandDTO(BaseModel):
    request_id: str = Field(..., description="Unique command tracking identifier")
    action: str = Field(..., description="Action type: HEALTH_CHECK, RECONNECT, DISCONNECT, LOGOUT, PREFLIGHT")
    status: str = Field(..., description="Lifecycle status: REQUESTED, CLAIMED, EXECUTING, COMPLETED, FAILED")
    version: int = Field(1, description="Monotonic version for atomic CAS claiming")
    requested_by: str = Field(..., description="Username of initiating operator")
    requested_at: str = Field(..., description="ISO 8601 UTC timestamp of request submission")
    claimed_by: Optional[str] = Field(None, description="Worker daemon identifier that claimed the lease")
    claimed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp when worker claimed the command")
    lease_expires_at: Optional[str] = Field(
        None,
        description="ISO 8601 UTC lease expiration timestamp (60s for standard commands; 120s for PREFLIGHT)",
    )
    executed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp when execution began")
    completed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp when command completed or failed")
    result: Optional[str] = Field(None, description="Execution outcome details upon success")
    error_message: Optional[str] = Field(None, description="Sanitized failure diagnostic message if failed")


class WhatsAppWorkerIdentityDTO(BaseModel):
    """Stable worker instance identity metadata. Safe for dashboard exposure."""
    instance_id: str = Field(..., description="Operator-assigned stable worker label (WORKER_INSTANCE_ID)")
    environment: str = Field(..., description="Deployment environment (production, staging, etc.)")
    arch: str = Field(..., description="CPU architecture (e.g. aarch64, x86_64)")
    os: str = Field(..., description="Operating system name and release")
    python_version: str = Field(..., description="Python runtime version")
    chrome_version: Optional[str] = Field(None, description="Chrome for Testing version string (binary read-only)")
    chromedriver_version: Optional[str] = Field(None, description="ChromeDriver version string (binary read-only)")
    xvfb_display: str = Field("", description="Configured DISPLAY environment variable")
    capabilities: List[str] = Field(default_factory=list, description="Confirmed worker capabilities")
    app_version: str = Field("", description="Application phase/version label")
    registered_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of runner startup")
    last_seen: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of last identity publication")
    worker_id: str = Field(..., description="Transient worker process identifier")


class WhatsAppWorkerHeartbeatDTO(BaseModel):
    """Infrastructure health snapshot published by the worker every heartbeat cycle."""
    instance_id: str = Field(..., description="Worker instance label")
    worker_id: str = Field(..., description="Transient worker process identifier")
    last_seen: str = Field(..., description="ISO 8601 UTC timestamp of last heartbeat")
    runner_state: str = Field(..., description="Current runner lifecycle state")
    uptime_seconds: float = Field(0.0, description="Runner process uptime in seconds")
    heartbeat_age_seconds: Optional[float] = Field(None, description="Seconds elapsed since last heartbeat")
    xvfb_healthy: bool = Field(False, description="True if Xvfb display responds to xdpyinfo probe")
    chrome_reachable: bool = Field(False, description="True if Chrome binary is discoverable on host")
    chromedriver_reachable: bool = Field(False, description="True if ChromeDriver binary is discoverable on host")
    session_profile_present: bool = Field(False, description="True if WhatsApp session profile directory contains data")
    provider_active: bool = Field(False, description="True if the WhatsApp Web provider is currently running")
    infra_health: WorkerInfraHealthEnum = Field(
        WorkerInfraHealthEnum.UNKNOWN,
        description="Computed infrastructure health: HEALTHY / DEGRADED / OFFLINE / UNKNOWN",
    )


class WhatsAppStatusDTO(BaseModel):
    # --- Session state (Phase 7.6, unchanged) ---
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
        description="Mandatory semantic disclaimer",
    )

    # --- Phase 7.7-B Step 7: Five independent health dimensions ---
    # Dimension 1: Infrastructure health
    worker_identity: Optional[WhatsAppWorkerIdentityDTO] = Field(
        None,
        description="Stable worker instance identity. None if worker has not published identity yet.",
    )
    worker_heartbeat: Optional[WhatsAppWorkerHeartbeatDTO] = Field(
        None,
        description="Infrastructure health snapshot from last worker heartbeat. None if no heartbeat received.",
    )
    infra_health: WorkerInfraHealthEnum = Field(
        WorkerInfraHealthEnum.UNKNOWN,
        description="Infrastructure-level health: HEALTHY / DEGRADED / OFFLINE / UNKNOWN",
    )

    # Dimension 2: Browser health (sourced from telemetry)
    browser_state: Optional[str] = Field(
        None,
        description="Browser/provider session state (CONNECTED, DISCONNECTED, AUTHENTICATING, etc.)",
    )

    # Dimension 3: WhatsApp session health (sourced from telemetry + profile)
    session_authenticated: bool = Field(
        False,
        description="True if the WhatsApp session is actively authenticated (no QR required)",
    )
    qr_required: bool = Field(
        False,
        description="True if QR code authentication is needed",
    )

    # Dimensions 4 (runner) and 5 (queue) are covered by existing runner_* fields and queue endpoint.
    # last_preflight_result: most recently executed PREFLIGHT outcome
    last_preflight_result: Optional[Dict[str, Any]] = Field(
        None,
        description="Result of last PREFLIGHT command execution. None if never run.",
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


class WhatsAppPreflightRequest(BaseModel):
    """Request body for PREFLIGHT command. No parameters required; all defaults are safe."""
    reason: str = Field(
        default="Operator-initiated diagnostic preflight",
        min_length=3,
        max_length=255,
        description="Optional operational rationale for the preflight check",
    )
