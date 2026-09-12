"""
Analytics Web Service for Phase 7.5 Analytics & Reporting Control Center.

Coordinates between database queries, calendar timezone normalization in APP_TIMEZONE,
Pydantic DTO serialization, fail-safe streaming CSV exports, and audit logging.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Generator, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.connection import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.contact import Contact
from app.models.message import Message
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.services.analytics_service import AnalyticsService
from app.utils.timezone import resolve_calendar_range, CalendarRange
from app.utils.settings import settings
from app.web.services.contact_service import mask_phone_number
from app.web.schemas.analytics import (
    DateRangeInfo,
    ThroughputPoint,
    CampaignPerformanceItem,
    QueueLiveAnalyticsResponse,
    QueueHistoricalAnalyticsResponse,
    CampaignAnalyticsResponse,
    CampaignAnalyticsPerformance,
    OverviewKPIs,
    AnalyticsOverviewResponse,
)


class AnalyticsWebService:
    """High-level service powering Analytics & Reporting web APIs and views."""

    @staticmethod
    def _format_date_range_info(cal_range: CalendarRange) -> DateRangeInfo:
        return DateRangeInfo(
            preset=cal_range.preset,
            timezone=cal_range.timezone_name,
            start_date_utc=cal_range.start_utc.isoformat(),
            end_date_utc=cal_range.end_utc.isoformat(),
            start_date_local=cal_range.start_local_str,
            end_date_local=cal_range.end_local_str,
        )

    @classmethod
    def get_overview(
        cls,
        db: Session,
        preset: str = "today",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> AnalyticsOverviewResponse:
        """
        Calculates executive overview analytics across the specified calendar preset in APP_TIMEZONE.
        Combines bounded historical performance with point-in-time live queue health.
        """
        cal_range = resolve_calendar_range(
            preset=preset,
            start_date=start_date,
            end_date=end_date,
        )
        date_range_info = cls._format_date_range_info(cal_range)

        # Bounded overview analytics over [start_utc, end_utc)
        raw_overview = AnalyticsService.get_overview_analytics(
            db=db,
            start_date=cal_range.start_utc,
            end_date=cal_range.end_utc,
        )

        kpi_raw = raw_overview.get("kpis", {})
        kpis = OverviewKPIs(
            confirmed_sends=kpi_raw.get("confirmed_sends", 0),
            confirmed_send_rate=kpi_raw.get("confirmed_send_rate", 0.0),
            permanent_failures=kpi_raw.get("permanent_failures", 0),
            unknown_outcomes=kpi_raw.get("unknown_outcomes", 0),
            active_campaign_count=raw_overview.get("active_campaign_count", 0),
        )

        timeline: List[ThroughputPoint] = [
            ThroughputPoint(
                date=pt.get("date", ""),
                hour=pt.get("hour"),
                confirmed_sends=pt.get("confirmed_sends", 0),
            )
            for pt in raw_overview.get("throughput_timeline", [])
        ]

        campaign_perf: List[CampaignPerformanceItem] = [
            CampaignPerformanceItem(
                campaign_id=item.get("campaign_id", 0),
                name=item.get("name", ""),
                status=item.get("status", ""),
                confirmed_sends=item.get("confirmed_sends", 0),
                failed=item.get("failed", 0),
                unknown_outcome=item.get("unknown_outcome", 0),
                confirmed_send_rate=item.get("confirmed_send_rate", 0.0),
                contact_completion_percentage=item.get("contact_completion_percentage", 0.0),
                queue_terminal_percentage=item.get("queue_terminal_percentage", 0.0),
            )
            for item in raw_overview.get("campaign_performance", [])
        ]

        # Live point-in-time queue health (zero date filtering)
        raw_live = AnalyticsService.get_queue_live_analytics(db=db)
        live_snapshot = QueueLiveAnalyticsResponse(
            queued_count=raw_live.get("queued_count", 0),
            processing_count=raw_live.get("processing_count", 0),
            retry_pending_count=raw_live.get("retry_pending_count", 0),
            unknown_outcome_count=raw_live.get("unknown_outcome_count", 0),
            stale_leases_count=raw_live.get("stale_leases_count", 0),
            oldest_queued_age_seconds=raw_live.get("oldest_queued_age_seconds"),
            circuit_breaker_status=raw_live.get("circuit_breaker_status", "CLOSED"),
            emergency_stop_status=raw_live.get("emergency_stop_status", "INACTIVE"),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

        return AnalyticsOverviewResponse(
            date_range=date_range_info,
            kpis=kpis,
            throughput_timeline=timeline,
            campaign_performance=campaign_perf,
            live_queue_snapshot=live_snapshot,
        )

    @classmethod
    def get_campaign_analytics(
        cls,
        db: Session,
        campaign_id: int,
        preset: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[CampaignAnalyticsResponse]:
        """
        Calculates comprehensive campaign performance, contact distribution, and send rates.
        """
        date_range_info: Optional[DateRangeInfo] = None
        start_utc: Optional[datetime] = None
        end_utc: Optional[datetime] = None

        if preset:
            cal_range = resolve_calendar_range(
                preset=preset,
                start_date=start_date,
                end_date=end_date,
            )
            date_range_info = cls._format_date_range_info(cal_range)
            start_utc = cal_range.start_utc
            end_utc = cal_range.end_utc

        raw = AnalyticsService.get_campaign_analytics(
            db=db,
            campaign_id=campaign_id,
            start_date=start_utc,
            end_date=end_utc,
        )
        if not raw:
            return None

        perf_raw = raw.get("performance", {})
        performance = CampaignAnalyticsPerformance(
            confirmed_send_rate=perf_raw.get("confirmed_send_rate", 0.0),
            failure_rate=perf_raw.get("failure_rate", 0.0),
            contact_completion_percentage=perf_raw.get("contact_completion_percentage", 0.0),
            queue_terminal_percentage=perf_raw.get("queue_terminal_percentage", 0.0),
            completion_percentage=perf_raw.get("completion_percentage", 0.0),
            terminal_dispatches=perf_raw.get("terminal_dispatches", 0),
        )

        return CampaignAnalyticsResponse(
            campaign_id=raw["campaign_id"],
            name=raw["name"],
            status=raw["status"],
            started_at=raw.get("started_at"),
            completed_at=raw.get("completed_at"),
            duration_seconds=raw.get("duration_seconds"),
            total_contacts=raw.get("total_contacts", 0),
            total_messages=raw.get("total_messages", 0),
            contacts_breakdown=raw.get("contacts_breakdown", {}),
            queue_breakdown=raw.get("queue_breakdown", {}),
            performance=performance,
            pacing=raw.get("pacing", {}),
            trend=raw.get("trend", []),
            date_range=date_range_info,
        )

    @classmethod
    def get_queue_live(cls, db: Session) -> QueueLiveAnalyticsResponse:
        """Point-in-time live snapshot of current queue health (zero date filtering)."""
        raw = AnalyticsService.get_queue_live_analytics(db)
        return QueueLiveAnalyticsResponse(
            queued_count=raw.get("queued_count", 0),
            processing_count=raw.get("processing_count", 0),
            retry_pending_count=raw.get("retry_pending_count", 0),
            unknown_outcome_count=raw.get("unknown_outcome_count", 0),
            stale_leases_count=raw.get("stale_leases_count", 0),
            oldest_queued_age_seconds=raw.get("oldest_queued_age_seconds"),
            circuit_breaker_status=raw.get("circuit_breaker_status", "CLOSED"),
            emergency_stop_status=raw.get("emergency_stop_status", "INACTIVE"),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def get_queue_historical(
        cls,
        db: Session,
        preset: str = "today",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> QueueHistoricalAnalyticsResponse:
        """Aggregated execution metrics strictly within [start_utc, end_utc)."""
        cal_range = resolve_calendar_range(preset=preset, start_date=start_date, end_date=end_date)
        date_range_info = cls._format_date_range_info(cal_range)

        raw = AnalyticsService.get_queue_historical_analytics(
            db=db,
            start_date=cal_range.start_utc,
            end_date=cal_range.end_utc,
        )

        timeline = [
            ThroughputPoint(
                date=pt.get("date", ""),
                hour=pt.get("hour"),
                confirmed_sends=pt.get("confirmed_sends", 0),
            )
            for pt in raw.get("throughput_timeline", [])
        ]

        return QueueHistoricalAnalyticsResponse(
            date_range=date_range_info,
            dispatches_completed=raw.get("dispatches_completed", 0),
            permanent_failures=raw.get("permanent_failures", 0),
            unknown_outcomes=raw.get("unknown_outcomes", 0),
            confirmed_send_rate=raw.get("confirmed_send_rate", 0.0),
            average_lease_duration_seconds=raw.get("average_lease_duration_seconds", 0.0),
            retry_distribution=raw.get("retry_distribution", {}),
            throughput_timeline=timeline,
        )

    @classmethod
    def stream_campaign_csv(
        cls,
        campaign_id: int,
        user: User,
        db: Optional[Session] = None,
    ) -> Generator[str, None, None]:
        """
        Streams campaign message records as CSV adhering to the 5-stage fail-safe audit lifecycle.
        - OPERATOR role receives masked phone numbers (+201******678).
        - ADMIN and OWNER receive full E.164.
        - Message body content is STRICTLY OMITTED.
        - AuditLog is written in generator finally: block, recording SUCCESS on clean completion,
          or INTERRUPTED if client disconnects early.
        """
        row_count = 0
        stream_completed = False
        owns_session = db is None
        db_session = db if db is not None else SessionLocal()
        should_mask = user.role == UserRole.OPERATOR.value

        try:
            # Stage 3: Yield CSV header
            yield "message_id,campaign_id,contact_name,phone_e164,status,error_type,attempt_count,sent_at_utc,created_at_utc\r\n"

            # Stream message records via cursor batching
            query = (
                db_session.query(Message, Contact)
                .outerjoin(Contact, Message.contact_id == Contact.id)
                .filter(Message.campaign_id == campaign_id)
                .order_by(Message.id.asc())
                .yield_per(500)
            )

            for msg, contact in query:
                phone = contact.phone_e164 if contact else ""
                if phone and should_mask:
                    phone = mask_phone_number(phone)
                contact_name = (contact.name or "").replace('"', '""') if contact else ""
                sent_at = msg.sent_at.isoformat() if msg.sent_at else ""
                created_at = msg.created_at.isoformat() if msg.created_at else ""
                error_type = msg.error_type or ""

                line = f'{msg.id},{msg.campaign_id},"{contact_name}","{phone}",{msg.status},{error_type},{msg.attempt_count},"{sent_at}","{created_at}"\r\n'
                yield line
                row_count += 1

            # Stage 4: Clean cursor exhaustion reached
            stream_completed = True

        finally:
            # Stage 5: Finalize audit trail
            try:
                if stream_completed:
                    audit = AuditLog(
                        event_type="ANALYTICS_REPORT_EXPORTED",
                        campaign_id=campaign_id,
                        status="SUCCESS",
                        result=json.dumps({
                            "row_count": row_count,
                            "campaign_id": campaign_id,
                            "user_id": user.id,
                            "role": user.role,
                        }),
                    )
                else:
                    audit = AuditLog(
                        event_type="ANALYTICS_REPORT_EXPORT_FAILED",
                        campaign_id=campaign_id,
                        status="INTERRUPTED",
                        error_message="Stream interrupted before completion",
                        result=json.dumps({
                            "partial_row_count": row_count,
                            "campaign_id": campaign_id,
                            "user_id": user.id,
                            "role": user.role,
                        }),
                    )
                db_session.add(audit)
                db_session.commit()
            except Exception:
                db_session.rollback()
            finally:
                if owns_session:
                    db_session.close()

    @classmethod
    def stream_summary_csv(
        cls,
        preset: str,
        user: User,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Generator[str, None, None]:
        """
        Streams aggregated campaign summary performance as CSV with 5-stage fail-safe audit lifecycle.
        """
        row_count = 0
        stream_completed = False
        owns_session = db is None
        db_session = db if db is not None else SessionLocal()

        try:
            # Stage 3: Yield CSV header
            yield "campaign_id,campaign_name,status,total_contacts,confirmed_sends,failed,unknown_outcome,confirmed_send_rate,contact_completion_percentage,queue_terminal_percentage\r\n"

            cal_range = resolve_calendar_range(preset=preset, start_date=start_date, end_date=end_date)
            overview_data = AnalyticsService.get_overview_analytics(
                db=db_session,
                start_date=cal_range.start_utc,
                end_date=cal_range.end_utc,
            )

            for camp in overview_data.get("campaign_performance", []):
                cid = camp.get("campaign_id", 0)
                name = (camp.get("name", "")).replace('"', '""')
                status = camp.get("status", "")
                total = camp.get("total_contacts", 0)
                sends = camp.get("confirmed_sends", 0)
                failed = camp.get("failed", 0)
                unknown = camp.get("unknown_outcome", 0)
                rate = camp.get("confirmed_send_rate", 0.0)
                contact_pct = camp.get("contact_completion_percentage", 0.0)
                queue_pct = camp.get("queue_terminal_percentage", 0.0)

                line = f'{cid},"{name}",{status},{total},{sends},{failed},{unknown},{rate:.2f},{contact_pct:.2f},{queue_pct:.2f}\r\n'
                yield line
                row_count += 1

            stream_completed = True

        finally:
            try:
                if stream_completed:
                    audit = AuditLog(
                        event_type="ANALYTICS_REPORT_EXPORTED",
                        status="SUCCESS",
                        result=json.dumps({
                            "row_count": row_count,
                            "type": "summary",
                            "preset": preset,
                            "user_id": user.id,
                        }),
                    )
                else:
                    audit = AuditLog(
                        event_type="ANALYTICS_REPORT_EXPORT_FAILED",
                        status="INTERRUPTED",
                        error_message="Stream interrupted before completion",
                        result=json.dumps({
                            "partial_row_count": row_count,
                            "type": "summary",
                            "preset": preset,
                            "user_id": user.id,
                        }),
                    )
                db_session.add(audit)
                db_session.commit()
            except Exception:
                db_session.rollback()
            finally:
                if owns_session:
                    db_session.close()
