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
    def get_campaign_analytics(db: Session, campaign_id: int) -> Optional[Dict[str, Any]]:
        """
        Calculates comprehensive campaign performance, contact distribution, and
        the authoritative Confirmed Send Rate.
        """
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return None

        # Contact counts from CampaignContact
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
        msg_rows = (
            db.query(Message.status, Message.error_type, func.count(Message.id))
            .filter(Message.campaign_id == campaign_id)
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

        # Completion Percentage: (confirmed_sends + failed + unknown_outcome + skipped + excluded) / total * 100
        total_terminal = (
            confirmed_sends + failed_count + unknown_outcome_count + skipped_count + excluded_contacts
        )
        if total_contacts > 0:
            completion_percentage = round((total_terminal / total_contacts) * 100.0, 2)
        else:
            completion_percentage = 0.0

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

        return {
            "campaign_id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "started_at": start_time.isoformat() if start_time else None,
            "completed_at": end_time.isoformat() if end_time else None,
            "duration_seconds": duration_seconds,
            "total_contacts": total_contacts,
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
                "completion_percentage": completion_percentage,
                "terminal_dispatches": terminal_denominator,
            },
            "pacing": {
                "min_delay_seconds": campaign.min_delay_seconds,
                "max_delay_seconds": campaign.max_delay_seconds,
                "daily_limit": campaign.daily_limit,
                "error_threshold": campaign.error_threshold,
                "consecutive_errors": getattr(campaign, "consecutive_errors", 0),
            },
        }

    @staticmethod
    def get_queue_analytics(db: Session, campaign_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Calculates queue backlog, in-flight leases, stale leases, throughput, and state distribution.
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
