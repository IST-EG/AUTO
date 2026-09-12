"""
Pydantic schemas and DTOs for Phase 7.5 Analytics & Reporting Control Center.

All fields adhere strictly to the Phase 7.5 Analytics Semantic Matrix:
- CONFIRMED SEND RATE (confirmed_send_rate) is strictly calculated from
  UI-confirmed message dispatches.
- NEVER implies delivery, read receipts, or recipient device state.
- CARDINALITY-SAFE campaign completion rates are calculated exclusively from campaign_contacts.
- CURRENT QUEUE HEALTH is point-in-time snapshot with zero date filtering.
- HISTORICAL EXECUTION ANALYTICS is time-windowed.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class DateRangeInfo(BaseModel):
    """Encapsulates calendar range boundaries resolved by backend in APP_TIMEZONE."""
    preset: str = Field(..., description="Preset name: today, yesterday, last_7_days, last_30_days, custom")
    timezone: str = Field(..., description="Authoritative application timezone, e.g. Africa/Cairo")
    start_date_utc: str = Field(..., description="UTC start timestamp (inclusive ISO 8601)")
    end_date_utc: str = Field(..., description="UTC end timestamp (exclusive ISO 8601)")
    start_date_local: str = Field(..., description="Local start date/time string in APP_TIMEZONE")
    end_date_local: str = Field(..., description="Local end date/time string in APP_TIMEZONE")


class ThroughputPoint(BaseModel):
    """Single data point for timeline throughput charts."""
    date: str = Field(..., description="Date or bucket label (YYYY-MM-DD or YYYY-MM-DD HH:00)")
    hour: Optional[int] = Field(None, description="Hour of day (0-23) when hourly resolution is used")
    confirmed_sends: int = Field(0, description="Confirmed message dispatches in this bucket")


class CampaignPerformanceItem(BaseModel):
    """Aggregated campaign performance row for overview analytics table."""
    campaign_id: int = Field(..., description="Campaign identifier")
    name: str = Field(..., description="Campaign name")
    status: str = Field(..., description="Authoritative campaign status (DRAFT, SCHEDULED, RUNNING, PAUSED, COMPLETED, CANCELLED, FAILED)")
    confirmed_sends: int = Field(0, description="Dispatches reaching confirmed sent status in window")
    failed: int = Field(0, description="Dispatches reaching permanent failure in window")
    unknown_outcome: int = Field(0, description="Dispatches with ambiguous outcome requiring verification")
    confirmed_send_rate: float = Field(
        0.0,
        description="confirmed_sends / (confirmed_sends + failed + unknown_outcome) * 100"
    )
    contact_completion_percentage: float = Field(
        0.0,
        description="Cardinality-safe contact outreach completion percentage (from campaign_contacts)"
    )
    queue_terminal_percentage: float = Field(
        0.0,
        description="Queue terminal percentage (from messages table)"
    )


class QueueLiveAnalyticsResponse(BaseModel):
    """
    Current Queue Health (Live Point-in-Time Snapshot).
    Evaluated with ZERO date filtering.
    """
    queued_count: int = Field(0, description="Messages currently staged in QUEUED")
    processing_count: int = Field(0, description="Active worker leases in PROCESSING")
    retry_pending_count: int = Field(0, description="Messages awaiting exponential backoff in RETRY_PENDING")
    unknown_outcome_count: int = Field(0, description="Unreconciled dispatches requiring physical verification")
    stale_leases_count: int = Field(0, description="Messages locked in PROCESSING longer than stale threshold (120s)")
    oldest_queued_age_seconds: Optional[float] = Field(None, description="Age in seconds of oldest staged message in QUEUED")
    circuit_breaker_status: str = Field("CLOSED", description="Circuit breaker state: CLOSED or OPEN")
    emergency_stop_status: str = Field("INACTIVE", description="Emergency stop status: ACTIVE or INACTIVE")
    timestamp_utc: str = Field(..., description="UTC timestamp of snapshot generation")


class QueueHistoricalAnalyticsResponse(BaseModel):
    """
    Historical Execution Analytics.
    Aggregated execution metrics strictly over the bounded [start_date, end_date) interval.
    """
    date_range: DateRangeInfo
    dispatches_completed: int = Field(0, description="Messages reaching SENT status in window")
    permanent_failures: int = Field(0, description="Messages reaching FAILED (non-unknown) status in window")
    unknown_outcomes: int = Field(0, description="Messages reaching FAILED with UNKNOWN_OUTCOME in window")
    confirmed_send_rate: float = Field(
        0.0,
        description="confirmed_sends / (confirmed_sends + permanent_failures + unknown_outcomes) * 100"
    )
    average_lease_duration_seconds: float = Field(
        0.0,
        description="AVG(sent_at - locked_at) for dispatches completed in window"
    )
    retry_distribution: Dict[str, int] = Field(
        default_factory=dict,
        description="Histogram of attempt counts (1_attempt, 2_attempts, 3_plus_attempts)"
    )
    throughput_timeline: List[ThroughputPoint] = Field(
        default_factory=list,
        description="Dispatch throughput breakdown across window"
    )


class CampaignAnalyticsPerformance(BaseModel):
    confirmed_send_rate: float = Field(0.0, description="confirmed_sends / (confirmed_sends + failed + unknown) * 100")
    failure_rate: float = Field(0.0, description="(failed + unknown) / (confirmed_sends + failed + unknown) * 100")
    contact_completion_percentage: float = Field(0.0, description="Evaluated strictly from campaign_contacts")
    queue_terminal_percentage: float = Field(0.0, description="Evaluated strictly from messages table")
    completion_percentage: float = Field(0.0, description="Backwards compatibility alias for contact completion")
    terminal_dispatches: int = Field(0, description="Total terminal dispatch attempts in denominator")


class CampaignAnalyticsResponse(BaseModel):
    """Detailed analytics response for a single campaign."""
    campaign_id: int
    name: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    total_contacts: int = Field(0, description="Total audience contacts linked to campaign")
    total_messages: int = Field(0, description="Total message records created for campaign")
    contacts_breakdown: Dict[str, int] = Field(default_factory=dict)
    queue_breakdown: Dict[str, int] = Field(default_factory=dict)
    performance: CampaignAnalyticsPerformance
    pacing: Dict[str, Any] = Field(default_factory=dict)
    trend: List[Dict[str, Any]] = Field(default_factory=list)
    date_range: Optional[DateRangeInfo] = None
    disclaimer: str = Field(
        default="CONFIRMED SEND RATE is calculated exclusively from UI-confirmed dispatches. WhatsApp Web does not provide delivery or read receipts.",
        description="Mandatory semantic clarification"
    )


class OverviewKPIs(BaseModel):
    confirmed_sends: int = Field(0, description="Messages confirmed sent in window")
    confirmed_send_rate: float = Field(0.0, description="confirmed_sends / (confirmed_sends + failed + unknown) * 100")
    permanent_failures: int = Field(0, description="Permanent failure messages in window")
    unknown_outcomes: int = Field(0, description="Unknown outcome messages in window")
    active_campaign_count: int = Field(0, description="Number of currently RUNNING, SCHEDULED, or PAUSED campaigns")


class AnalyticsOverviewResponse(BaseModel):
    """Executive analytics overview combining KPIs, timeline, performance table, and live queue snapshot."""
    date_range: DateRangeInfo
    kpis: OverviewKPIs
    throughput_timeline: List[ThroughputPoint] = Field(default_factory=list)
    campaign_performance: List[CampaignPerformanceItem] = Field(default_factory=list)
    live_queue_snapshot: QueueLiveAnalyticsResponse
    disclaimer: str = Field(
        default="CONFIRMED SEND RATE is calculated exclusively from UI-confirmed dispatches. WhatsApp Web does not provide delivery or read receipts.",
        description="Mandatory semantic clarification"
    )
