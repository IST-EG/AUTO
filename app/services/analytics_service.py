"""
Analytics Service for WhatsApp Outreach Automation.

Delivers high-performance, on-demand operational and business aggregations
with ZERO database schema modifications or migrations.
All calculations leverage existing indexes on campaigns, campaign_contacts,
messages, audit_logs, and app_settings.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.utils.settings import settings


class AnalyticsService:
    """Computes real-time operational and performance analytics on demand."""

    @staticmethod
    def get_campaign_analytics(
        db: Session,
        campaign_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Calculates comprehensive campaign performance, contact distribution, and
        the authoritative Confirmed Send Rate. Supports optional date range filtering [start_date, end_date).
        """
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return None

        # Contact counts from CampaignContact (Audience Level)
        total_contacts = (
            db.query(func.count(CampaignContact.id))
            .filter(CampaignContact.campaign_id == campaign_id)
            .scalar() or 0
        )

        contact_status_rows = (
            db.query(CampaignContact.status, func.count(CampaignContact.id))
            .filter(CampaignContact.campaign_id == campaign_id)
            .group_by(CampaignContact.status)
            .all()
        )
        contact_statuses = {row[0].upper(): row[1] for row in contact_status_rows}

        eligible_contacts = contact_statuses.get("ELIGIBLE", 0)
        excluded_contacts = contact_statuses.get("EXCLUDED", 0)
        pending_contacts = contact_statuses.get("PENDING", 0)

        # Message lifecycle breakdown from Message table
        msg_query = db.query(Message.status, Message.error_type, func.count(Message.id)).filter(
            Message.campaign_id == campaign_id
        )

        # If date range provided, filter messages within [start_date, end_date)
        if start_date and end_date:
            msg_query = msg_query.filter(
                or_(
                    and_(Message.status == "SENT", Message.sent_at >= start_date, Message.sent_at < end_date),
                    and_(Message.status == "FAILED", Message.failed_at >= start_date, Message.failed_at < end_date),
                    and_(
                        Message.status.in_(["QUEUED", "PROCESSING", "RETRY_PENDING", "PENDING", "CANCELLED", "SKIPPED"]),
                        Message.created_at >= start_date,
                        Message.created_at < end_date,
                    ),
                )
            )

        msg_rows = msg_query.group_by(Message.status, Message.error_type).all()

        queued_count = 0
        processing_count = 0
        retry_pending_count = 0
        confirmed_sends = 0
        failed_count = 0
        unknown_outcome_count = 0
        skipped_count = 0
        cancelled_count = 0

        for status_val, error_type_val, count_val in msg_rows:
            st = (status_val or "").upper()
            et = (error_type_val or "").upper()
            if st == "QUEUED":
                queued_count += count_val
            elif st == "PROCESSING":
                processing_count += count_val
            elif st == "RETRY_PENDING":
                retry_pending_count += count_val
            elif st == "SENT":
                confirmed_sends += count_val
            elif st == "FAILED":
                if et == "UNKNOWN_OUTCOME":
                    unknown_outcome_count += count_val
                else:
                    failed_count += count_val
            elif st == "SKIPPED":
                skipped_count += count_val
            elif st == "CANCELLED":
                cancelled_count += count_val

        # Confirmed Send Rate Calculation (LOCKED FORMULA):
        # confirmed_send_rate = confirmed_sends / (confirmed_sends + failed + unknown_outcome) * 100
        # RETRY_PENDING, SKIPPED, CANCELLED are strictly excluded from the denominator.
        terminal_denominator = confirmed_sends + failed_count + unknown_outcome_count
        if terminal_denominator > 0:
            confirmed_send_rate = round((confirmed_sends / terminal_denominator) * 100.0, 2)
            failure_rate = round(((failed_count + unknown_outcome_count) / terminal_denominator) * 100.0, 2)
        else:
            confirmed_send_rate = 0.0
            failure_rate = 0.0

        # Cardinality-Safe Contact Outreach Completion Rate (from campaign_contacts)
        contact_terminal = (
            db.query(func.count(CampaignContact.id))
            .filter(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.status.in_(["SENT", "FAILED", "SKIPPED", "EXCLUDED"]),
            )
            .scalar() or 0
        )
        if total_contacts > 0:
            contact_completion_percentage = round((contact_terminal / total_contacts) * 100.0, 2)
        else:
            contact_completion_percentage = 0.0

        # Legacy completion percentage (confirmed_sends + failed + unknown_outcome + skipped + excluded) / total * 100
        legacy_total_terminal = (
            confirmed_sends + failed_count + unknown_outcome_count + skipped_count + excluded_contacts
        )
        if total_contacts > 0:
            legacy_completion_percentage = round((legacy_total_terminal / total_contacts) * 100.0, 2)
        else:
            legacy_completion_percentage = 0.0

        # Queue Terminal Percentage (from Message table)
        total_messages = db.query(func.count(Message.id)).filter(Message.campaign_id == campaign_id).scalar() or 0
        terminal_messages = confirmed_sends + failed_count + unknown_outcome_count + skipped_count + cancelled_count
        if total_messages > 0:
            queue_terminal_percentage = round((terminal_messages / total_messages) * 100.0, 2)
        else:
            queue_terminal_percentage = 0.0

        # Duration in seconds
        duration_seconds: Optional[float] = None
        now_utc = datetime.now(timezone.utc)
        start_time = campaign.scheduled_start_at or campaign.created_at
        end_time = campaign.scheduled_end_at if campaign.status == "COMPLETED" else None

        if end_time and start_time:
            started = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time
            completed = end_time.replace(tzinfo=timezone.utc) if end_time.tzinfo is None else end_time
            duration_seconds = max(0.0, (completed - started).total_seconds())
        elif start_time:
            started = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time
            duration_seconds = max(0.0, (now_utc - started).total_seconds())

        # Daily trend histogram for this campaign
        trend_histogram: List[Dict[str, Any]] = []
        trend_query = db.query(
            func.date(Message.sent_at).label("d"),
            func.count(Message.id).label("c")
        ).filter(
            Message.campaign_id == campaign_id,
            Message.status == "SENT",
            Message.sent_at.isnot(None),
        )
        if start_date and end_date:
            trend_query = trend_query.filter(Message.sent_at >= start_date, Message.sent_at < end_date)
        trend_rows = trend_query.group_by(func.date(Message.sent_at)).order_by(func.date(Message.sent_at)).all()

        for d_val, count_val in trend_rows:
            d_str = str(d_val) if d_val else ""
            if d_str:
                trend_histogram.append({"date": d_str, "confirmed_sends": count_val})

        return {
            "campaign_id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "started_at": start_time.isoformat() if start_time else None,
            "completed_at": end_time.isoformat() if end_time else None,
            "duration_seconds": duration_seconds,
            "total_contacts": total_contacts,
            "total_messages": total_messages,
            "contacts_breakdown": {
                "eligible": eligible_contacts,
                "excluded": excluded_contacts,
                "pending": pending_contacts,
            },
            "queue_breakdown": {
                "queued": queued_count,
                "processing": processing_count,
                "retry_pending": retry_pending_count,
                "confirmed_sends": confirmed_sends,
                "failed": failed_count,
                "unknown_outcome": unknown_outcome_count,
                "skipped": skipped_count,
                "cancelled": cancelled_count,
            },
            "performance": {
                "confirmed_send_rate": confirmed_send_rate,
                "failure_rate": failure_rate,
                "completion_percentage": legacy_completion_percentage,  # Backwards compatibility alias
                "contact_completion_percentage": contact_completion_percentage,
                "queue_terminal_percentage": queue_terminal_percentage,
                "terminal_dispatches": terminal_denominator,
            },
            "pacing": {
                "min_delay_seconds": campaign.min_delay_seconds,
                "max_delay_seconds": campaign.max_delay_seconds,
                "daily_limit": campaign.daily_limit,
                "error_threshold": campaign.error_threshold,
                "consecutive_errors": getattr(campaign, "consecutive_errors", 0),
            },
            "trend": trend_histogram,
        }

    @staticmethod
    def get_overview_analytics(
        db: Session,
        start_date: datetime,
        end_date: datetime,
        campaign_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Aggregates system-wide (or campaign-scoped) dispatch analytics over half-open UTC interval [start_date, end_date).
        """
        # 1. Confirmed Sends in window
        sends_q = db.query(func.count(Message.id)).filter(
            Message.status == "SENT",
            Message.sent_at >= start_date,
            Message.sent_at < end_date,
        )
        if campaign_id is not None:
            sends_q = sends_q.filter(Message.campaign_id == campaign_id)
        confirmed_sends = sends_q.scalar() or 0

        # 2. Permanent Failures in window
        fail_q = db.query(func.count(Message.id)).filter(
            Message.status == "FAILED",
            or_(Message.error_type != "UNKNOWN_OUTCOME", Message.error_type.is_(None)),
            Message.failed_at >= start_date,
            Message.failed_at < end_date,
        )
        if campaign_id is not None:
            fail_q = fail_q.filter(Message.campaign_id == campaign_id)
        permanent_failures = fail_q.scalar() or 0

        # 3. UNKNOWN_OUTCOME incidents in window
        unknown_q = db.query(func.count(Message.id)).filter(
            Message.status == "FAILED",
            Message.error_type == "UNKNOWN_OUTCOME",
            Message.failed_at >= start_date,
            Message.failed_at < end_date,
        )
        if campaign_id is not None:
            unknown_q = unknown_q.filter(Message.campaign_id == campaign_id)
        unknown_outcomes = unknown_q.scalar() or 0

        # Confirmed Send Rate (Exact locked formula)
        terminal_denominator = confirmed_sends + permanent_failures + unknown_outcomes
        if terminal_denominator > 0:
            confirmed_send_rate = round((confirmed_sends / terminal_denominator) * 100.0, 2)
        else:
            confirmed_send_rate = 0.0

        # 4. Throughput Timeline: Daily grouping of sent vs failed
        timeline_dict: Dict[str, Dict[str, int]] = {}

        # Daily SENT
        sent_daily_q = db.query(
            func.date(Message.sent_at).label("d"),
            func.count(Message.id).label("c")
        ).filter(
            Message.status == "SENT",
            Message.sent_at >= start_date,
            Message.sent_at < end_date,
        )
        if campaign_id is not None:
            sent_daily_q = sent_daily_q.filter(Message.campaign_id == campaign_id)
        sent_daily = sent_daily_q.group_by(func.date(Message.sent_at)).all()

        for d_val, c_val in sent_daily:
            d_str = str(d_val) if d_val else ""
            if d_str:
                if d_str not in timeline_dict:
                    timeline_dict[d_str] = {"confirmed_sends": 0, "failed": 0, "unknown_outcome": 0}
                timeline_dict[d_str]["confirmed_sends"] += c_val

        # Daily FAILED & UNKNOWN_OUTCOME
        failed_daily_q = db.query(
            func.date(Message.failed_at).label("d"),
            Message.error_type,
            func.count(Message.id).label("c")
        ).filter(
            Message.status == "FAILED",
            Message.failed_at >= start_date,
            Message.failed_at < end_date,
        )
        if campaign_id is not None:
            failed_daily_q = failed_daily_q.filter(Message.campaign_id == campaign_id)
        failed_daily = failed_daily_q.group_by(func.date(Message.failed_at), Message.error_type).all()

        for d_val, et_val, c_val in failed_daily:
            d_str = str(d_val) if d_val else ""
            if d_str:
                if d_str not in timeline_dict:
                    timeline_dict[d_str] = {"confirmed_sends": 0, "failed": 0, "unknown_outcome": 0}
                if (et_val or "").upper() == "UNKNOWN_OUTCOME":
                    timeline_dict[d_str]["unknown_outcome"] += c_val
                else:
                    timeline_dict[d_str]["failed"] += c_val

        timeline = [
            {
                "date": d,
                "confirmed_sends": stats["confirmed_sends"],
                "failed": stats["failed"],
                "unknown_outcome": stats["unknown_outcome"],
            }
            for d, stats in sorted(timeline_dict.items())
        ]

        # 5. Campaign Performance summary table
        campaigns_query = db.query(Campaign)
        if campaign_id is not None:
            campaigns_query = campaigns_query.filter(Campaign.id == campaign_id)
        all_campaigns = campaigns_query.order_by(Campaign.id.desc()).all()

        campaigns_summary = []
        for camp in all_campaigns:
            c_data = AnalyticsService.get_campaign_analytics(db, camp.id, start_date=start_date, end_date=end_date)
            if c_data:
                perf = c_data.get("performance", {})
                q_bd = c_data.get("queue_breakdown", {})
                campaigns_summary.append({
                    "id": camp.id,
                    "campaign_id": camp.id,
                    "name": camp.name,
                    "status": camp.status,
                    "total_contacts": c_data.get("total_contacts", 0),
                    "confirmed_sends": q_bd.get("confirmed_sends", 0),
                    "failed": q_bd.get("failed", 0),
                    "unknown_outcome": q_bd.get("unknown_outcome", 0),
                    "retry_pending": q_bd.get("retry_pending", 0),
                    "confirmed_send_rate": perf.get("confirmed_send_rate", 0.0),
                    "contact_completion_percentage": perf.get("contact_completion_percentage", 0.0),
                    "queue_terminal_percentage": perf.get("queue_terminal_percentage", 0.0),
                })

        return {
            "kpis": {
                "confirmed_sends": confirmed_sends,
                "confirmed_send_rate": confirmed_send_rate,
                "permanent_failures": permanent_failures,
                "failed": permanent_failures,
                "unknown_outcomes": unknown_outcomes,
                "unknown_outcome": unknown_outcomes,
                "total_terminal": terminal_denominator,
            },
            "throughput_timeline": timeline,
            "campaign_performance": campaigns_summary,
            "campaigns": campaigns_summary,
            "active_campaign_count": len([c for c in all_campaigns if c.status in ["RUNNING", "SCHEDULED", "PAUSED"]]),
        }

    @staticmethod
    def get_queue_live_analytics(db: Session) -> Dict[str, Any]:
        """
        Current Queue Health: Point-in-time snapshot with ZERO date filtering.
        """
        now_utc = datetime.now(timezone.utc)
        stale_cutoff = now_utc - timedelta(seconds=120)

        # Status counts
        status_rows = db.query(Message.status, Message.error_type, func.count(Message.id)).group_by(
            Message.status, Message.error_type
        ).all()

        queued_count = 0
        processing_count = 0
        retry_pending_count = 0
        unknown_outcome_count = 0

        for st_val, et_val, c_val in status_rows:
            st = (st_val or "").upper()
            et = (et_val or "").upper()
            if st == "QUEUED":
                queued_count += c_val
            elif st == "PROCESSING":
                processing_count += c_val
            elif st == "RETRY_PENDING":
                retry_pending_count += c_val
            elif st == "FAILED" and et == "UNKNOWN_OUTCOME":
                unknown_outcome_count += c_val

        # Stale leases (>120s)
        stale_leases_count = (
            db.query(func.count(Message.id))
            .filter(Message.status == "PROCESSING", Message.locked_at < stale_cutoff)
            .scalar() or 0
        )

        # Oldest queued message age
        oldest_queued = (
            db.query(func.min(Message.queued_at))
            .filter(Message.status == "QUEUED")
            .scalar()
        )
        oldest_age_seconds: Optional[float] = None
        if oldest_queued:
            oq_utc = oldest_queued.replace(tzinfo=timezone.utc) if oldest_queued.tzinfo is None else oldest_queued
            oldest_age_seconds = max(0.0, (now_utc - oq_utc).total_seconds())

        # Circuit breaker status
        paused_count = db.query(func.count(Campaign.id)).filter(Campaign.status == "PAUSED").scalar() or 0
        circuit_breaker_status = "OPEN" if paused_count > 0 else "CLOSED"

        # Emergency stop status from AppSetting
        setting = db.query(AppSetting).filter(AppSetting.key == "emergency_stop").first()
        e_stop_active = False
        if setting and setting.value:
            import json
            try:
                data = json.loads(setting.value)
                e_stop_active = bool(data.get("active", False))
            except Exception:
                pass

        return {
            "queued_count": queued_count,
            "processing_count": processing_count,
            "retry_pending_count": retry_pending_count,
            "unknown_outcome_count": unknown_outcome_count,
            "stale_leases_count": stale_leases_count,
            "oldest_queued_age_seconds": round(oldest_age_seconds, 1) if oldest_age_seconds is not None else None,
            "circuit_breaker_status": circuit_breaker_status,
            "emergency_stop_status": "ACTIVE" if e_stop_active else "INACTIVE",
        }

    @staticmethod
    def get_queue_historical_analytics(
        db: Session,
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, Any]:
        """
        Historical Execution Analytics: Time-windowed execution metrics strictly over [start_date, end_date).
        """
        # Dispatches completed in window
        confirmed_sends = (
            db.query(func.count(Message.id))
            .filter(Message.status == "SENT", Message.sent_at >= start_date, Message.sent_at < end_date)
            .scalar() or 0
        )

        # Permanent failures in window
        permanent_failures = (
            db.query(func.count(Message.id))
            .filter(
                Message.status == "FAILED",
                or_(Message.error_type != "UNKNOWN_OUTCOME", Message.error_type.is_(None)),
                Message.failed_at >= start_date,
                Message.failed_at < end_date,
            )
            .scalar() or 0
        )

        # Unknown outcomes in window
        unknown_outcomes = (
            db.query(func.count(Message.id))
            .filter(
                Message.status == "FAILED",
                Message.error_type == "UNKNOWN_OUTCOME",
                Message.failed_at >= start_date,
                Message.failed_at < end_date,
            )
            .scalar() or 0
        )

        # Confirmed Send Rate
        terminal = confirmed_sends + permanent_failures + unknown_outcomes
        rate = round((confirmed_sends / terminal) * 100.0, 2) if terminal > 0 else 0.0

        # Average Lease Duration: AVG(sent_at - locked_at) for SENT messages in window
        # Dialect-safe Python computation over lightweight timestamp tuples
        lease_rows = (
            db.query(Message.sent_at, Message.locked_at)
            .filter(
                Message.status == "SENT",
                Message.sent_at >= start_date,
                Message.sent_at < end_date,
                Message.locked_at.isnot(None),
            )
            .all()
        )
        total_duration = 0.0
        valid_leases = 0
        for sent_dt, locked_dt in lease_rows:
            if sent_dt and locked_dt:
                s = sent_dt.replace(tzinfo=timezone.utc) if sent_dt.tzinfo is None else sent_dt
                l = locked_dt.replace(tzinfo=timezone.utc) if locked_dt.tzinfo is None else locked_dt
                diff = (s - l).total_seconds()
                if diff >= 0:
                    total_duration += diff
                    valid_leases += 1
        avg_lease_duration = round(total_duration / valid_leases, 2) if valid_leases > 0 else 0.0

        # Retry attempt distribution in window
        retry_rows = (
            db.query(Message.attempt_count, func.count(Message.id))
            .filter(Message.status == "SENT", Message.sent_at >= start_date, Message.sent_at < end_date)
            .group_by(Message.attempt_count)
            .all()
        )
        retry_distribution = {"1_attempt": 0, "2_attempts": 0, "3_plus_attempts": 0}
        for att, count_val in retry_rows:
            if att == 1:
                retry_distribution["1_attempt"] += count_val
            elif att == 2:
                retry_distribution["2_attempts"] += count_val
            elif att >= 3:
                retry_distribution["3_plus_attempts"] += count_val

        return {
            "dispatches_completed": confirmed_sends,
            "permanent_failures": permanent_failures,
            "unknown_outcomes": unknown_outcomes,
            "confirmed_send_rate": rate,
            "average_lease_duration_seconds": avg_lease_duration,
            "retry_distribution": retry_distribution,
        }

    @staticmethod
    def get_queue_analytics(db: Session, campaign_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Calculates queue backlog, in-flight leases, stale leases, throughput, and state distribution.
        Maintained for backwards compatibility with Phase 6 CLI and Dashboard.
        """
        now_utc = datetime.now(timezone.utc)
        lease_timeout_seconds = 300  # 5 minutes lease cutoff
        stale_cutoff = now_utc - timedelta(seconds=lease_timeout_seconds)

        query = db.query(Message)
        if campaign_id is not None:
            query = query.filter(Message.campaign_id == campaign_id)

        msg_rows = (
            query.with_entities(Message.status, Message.error_type, func.count(Message.id))
            .group_by(Message.status, Message.error_type)
            .all()
        )

        queued_count = 0
        processing_count = 0
        retry_pending_count = 0
        confirmed_sends = 0
        failed_count = 0
        unknown_outcome_count = 0
        skipped_count = 0
        cancelled_count = 0

        for status_val, error_type_val, count_val in msg_rows:
            st = (status_val or "").upper()
            et = (error_type_val or "").upper()
            if st == "QUEUED":
                queued_count += count_val
            elif st == "PROCESSING":
                processing_count += count_val
            elif st == "RETRY_PENDING":
                retry_pending_count += count_val
            elif st == "SENT":
                confirmed_sends += count_val
            elif st == "FAILED":
                if et == "UNKNOWN_OUTCOME":
                    unknown_outcome_count += count_val
                else:
                    failed_count += count_val
            elif st == "SKIPPED":
                skipped_count += count_val
            elif st == "CANCELLED":
                cancelled_count += count_val

        # Stale leases count (PROCESSING messages locked before cutoff)
        stale_query = db.query(func.count(Message.id)).filter(
            Message.status == "PROCESSING",
            Message.locked_at < stale_cutoff,
        )
        if campaign_id is not None:
            stale_query = stale_query.filter(Message.campaign_id == campaign_id)
        stale_leases_count = stale_query.scalar() or 0

        # Throughput calculations (confirmed dispatches over 1h, 6h, 24h)
        one_hour_ago = now_utc - timedelta(hours=1)
        six_hours_ago = now_utc - timedelta(hours=6)
        twenty_four_hours_ago = now_utc - timedelta(hours=24)

        def _count_throughput(cutoff: datetime) -> int:
            tq = db.query(func.count(Message.id)).filter(
                Message.status == "SENT",
                Message.sent_at >= cutoff,
            )
            if campaign_id is not None:
                tq = tq.filter(Message.campaign_id == campaign_id)
            return tq.scalar() or 0

        confirmed_1h = _count_throughput(one_hour_ago)
        confirmed_6h = _count_throughput(six_hours_ago)
        confirmed_24h = _count_throughput(twenty_four_hours_ago)

        return {
            "campaign_id": campaign_id,
            "backlog": {
                "queued": queued_count,
                "processing": processing_count,
                "retry_pending": retry_pending_count,
                "stale_leases": stale_leases_count,
            },
            "terminal": {
                "confirmed_sends": confirmed_sends,
                "failed": failed_count,
                "unknown_outcome": unknown_outcome_count,
                "skipped": skipped_count,
                "cancelled": cancelled_count,
            },
            "confirmed_throughput": {
                "last_1_hour": confirmed_1h,
                "last_6_hours": confirmed_6h,
                "last_24_hours": confirmed_24h,
            },
            "health_warnings": {
                "has_stale_leases": stale_leases_count > 0,
                "has_unknown_outcome": unknown_outcome_count > 0,
            },
        }

    @staticmethod
    def get_runner_analytics(db: Session) -> Dict[str, Any]:
        """
        Inspects process lock file and database heartbeat for runner liveliness,
        uptime, heartbeat age, and session throughput.
        """
        process_lock = ProcessLock(db=db)
        runner_info = process_lock.get_active_runner_info()

        if not runner_info:
            return {
                "is_running": False,
                "state": "STOPPED",
                "pid": None,
                "worker_id": None,
                "campaign_id": None,
                "started_at": None,
                "uptime_seconds": 0.0,
                "heartbeat_age_seconds": None,
                "is_stale": False,
                "dispatches_completed": 0,
            }

        pid = runner_info.get("pid")
        alive = is_pid_alive(pid) if pid else False

        now_utc = datetime.now(timezone.utc)
        heartbeat_str = runner_info.get("heartbeat_at") or runner_info.get("started_at")
        heartbeat_age = 0.0
        if heartbeat_str:
            try:
                hb_dt = datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                heartbeat_age = max(0.0, (now_utc - hb_dt).total_seconds())
            except Exception:
                pass

        started_str = runner_info.get("started_at")
        uptime = 0.0
        if started_str:
            try:
                start_dt = datetime.fromisoformat(started_str.replace("Z", "+00:00"))
                uptime = max(0.0, (now_utc - start_dt).total_seconds())
            except Exception:
                pass

        state = "RUNNING" if alive and heartbeat_age <= 60 else "UNHEALTHY" if not alive else "DEGRADED"

        return {
            "is_running": alive,
            "state": state,
            "pid": pid,
            "worker_id": runner_info.get("worker_id"),
            "campaign_id": runner_info.get("campaign_id"),
            "started_at": started_str,
            "uptime_seconds": round(uptime, 1),
            "heartbeat_age_seconds": round(heartbeat_age, 1),
            "is_stale": heartbeat_age > 60,
            "dispatches_completed": runner_info.get("sent_count", 0),
        }

    @staticmethod
    def get_provider_analytics(db: Session) -> Dict[str, Any]:
        """
        Analyzes provider profile status and distribution of message attempts,
        distinguishing temporary failures, permanent failures, and ambiguous outcomes.
        """
        import os
        from pathlib import Path

        session_path = getattr(settings, "WHATSAPP_SESSION_PATH", "./data/whatsapp_session")
        session_exists = os.path.exists(session_path) and os.path.isdir(session_path)
        has_profile_data = False
        if session_exists:
            try:
                has_profile_data = any(Path(session_path).iterdir())
            except Exception:
                pass

        # Total attempts, confirmed sends, failures, unknown outcomes
        total_attempts = db.query(func.count(Message.id)).filter(Message.attempt_count > 0).scalar() or 0
        confirmed_sends = db.query(func.count(Message.id)).filter(Message.status == "SENT").scalar() or 0
        unknown_outcomes = (
            db.query(func.count(Message.id))
            .filter(Message.status == "FAILED", Message.error_type == "UNKNOWN_OUTCOME")
            .scalar() or 0
        )
        permanent_failures = (
            db.query(func.count(Message.id))
            .filter(Message.status == "FAILED", or_(Message.error_type != "UNKNOWN_OUTCOME", Message.error_type.is_(None)))
            .scalar() or 0
        )
        temporary_retries = (
            db.query(func.count(Message.id))
            .filter(Message.attempt_count > 1)
            .scalar() or 0
        )

        terminal_denom = confirmed_sends + permanent_failures + unknown_outcomes
        rate = round((confirmed_sends / terminal_denom) * 100.0, 2) if terminal_denom > 0 else 0.0

        return {
            "provider_name": "WhatsAppWebProvider",
            "session": {
                "profile_path": session_path,
                "profile_dir_exists": session_exists,
                "has_profile_data": has_profile_data,
                "headless_mode": getattr(settings, "WHATSAPP_HEADLESS", False),
            },
            "dispatch_distribution": {
                "total_attempted_messages": total_attempts,
                "confirmed_sends": confirmed_sends,
                "temporary_retry_count": temporary_retries,
                "permanent_failures": permanent_failures,
                "unknown_outcomes": unknown_outcomes,
            },
            "confirmed_send_rate": rate,
        }

    @staticmethod
    def get_system_analytics(db: Session) -> Dict[str, Any]:
        """
        High-level executive dashboard combining runner state, queue backlog,
        emergency stop status, and campaign progression.
        """
        # Emergency stop status from AppSetting
        setting = db.query(AppSetting).filter(AppSetting.key == "emergency_stop").first()
        e_stop_active = False
        e_stop_reason = None
        if setting and setting.value:
            import json
            try:
                data = json.loads(setting.value)
                e_stop_active = bool(data.get("active", False))
                e_stop_reason = data.get("reason")
            except Exception:
                pass

        runner_analytics = AnalyticsService.get_runner_analytics(db)
        queue_analytics = AnalyticsService.get_queue_analytics(db)

        # Campaigns breakdown
        total_campaigns = db.query(func.count(Campaign.id)).scalar() or 0
        active_campaigns = db.query(func.count(Campaign.id)).filter(Campaign.status == "RUNNING").scalar() or 0
        paused_campaigns = db.query(func.count(Campaign.id)).filter(Campaign.status == "PAUSED").scalar() or 0
        completed_campaigns = db.query(func.count(Campaign.id)).filter(Campaign.status == "COMPLETED").scalar() or 0

        # Global daily throughput check against limit
        today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = (
            db.query(func.count(Message.id))
            .filter(Message.status == "SENT", Message.sent_at >= today_utc)
            .scalar() or 0
        )

        daily_limit = getattr(settings, "GLOBAL_DAILY_LIMIT", 100)
        daily_capacity_remaining = max(0, daily_limit - sent_today)

        return {
            "emergency_stop": {
                "active": e_stop_active,
                "reason": e_stop_reason,
            },
            "active_runner": runner_analytics,
            "queue_summary": {
                "depth": queue_analytics["backlog"]["queued"],
                "processing": queue_analytics["backlog"]["processing"],
                "retry_backlog": queue_analytics["backlog"]["retry_pending"],
                "stale_leases": queue_analytics["backlog"]["stale_leases"],
                "unknown_outcomes": queue_analytics["terminal"]["unknown_outcome"],
                "confirmed_24h": queue_analytics["confirmed_throughput"]["last_24_hours"],
            },
            "campaigns": {
                "total": total_campaigns,
                "running": active_campaigns,
                "paused": paused_campaigns,
                "completed": completed_campaigns,
            },
            "daily_quota": {
                "sent_today": sent_today,
                "global_daily_limit": daily_limit,
                "capacity_remaining": daily_capacity_remaining,
            },
        }
