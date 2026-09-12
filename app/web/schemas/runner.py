"""
Pydantic schemas for Runner Control and System Emergency Operations.
"""

from typing import Optional
from pydantic import BaseModel, Field


class RunnerStartRequest(BaseModel):
    campaign_id: int = Field(..., gt=0, description="Target campaign ID to process")


class RunnerStopRequest(BaseModel):
    timeout_seconds: int = Field(15, ge=1, le=60, description="Seconds to wait for safe send completion")


class RunnerStatusDTO(BaseModel):
    is_running: bool = Field(..., description="Whether a runner process PID is actively running")
    state: str = Field(..., description="Runner state: RUNNING, STOPPED, DEGRADED, UNHEALTHY, STALE_LOCK_DETECTED")
    pid: Optional[int] = Field(None, description="Operating system Process ID")
    worker_id: Optional[str] = Field(None, description="Worker identifier")
    campaign_id: Optional[int] = Field(None, description="Target campaign ID")
    started_at: Optional[str] = Field(None, description="Runner start time ISO timestamp")
    last_heartbeat: Optional[str] = Field(None, description="Last recorded heartbeat ISO timestamp")
    heartbeat_age_seconds: Optional[float] = Field(None, description="Elapsed seconds since heartbeat")
    uptime_seconds: float = Field(0.0, description="Runtime duration in seconds")
    is_stale: bool = Field(False, description="True if heartbeat age > 60s")
    lock_authoritative: bool = Field(True, description="Enforced by OS file lock data/runner.lock")
    desired_state: Optional[str] = Field(None, description="Desired runner state (STOPPED or RUNNING)")
    desired_campaign_id: Optional[int] = Field(None, description="Desired target campaign ID")


class EmergencyStopRequest(BaseModel):
    reason: Optional[str] = Field(
        default="Operator triggered emergency stop via Web Control Center",
        max_length=255,
        description="Justification for halting operations"
    )


class EmergencyResumeRequest(BaseModel):
    reason: Optional[str] = Field(
        default="Operator resumed operations via Web Control Center",
        max_length=255,
        description="Justification for resuming operations"
    )
