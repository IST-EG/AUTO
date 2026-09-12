"""
Dashboard Aggregation Application Service.

Delivers a unified, read-only operational snapshot for the Web Control Center.
Reuses existing domain services (AnalyticsService, evaluate_system_health,
EmergencyStop, ProcessLock, RunnerControlService) without duplicating business logic
or executing N+1 queries.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.campaign import Campaign
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.services.analytics_service import AnalyticsService
from app.readiness.health import evaluate_system_health, HealthState
from app.scheduler.emergency_stop import EmergencyStop
from app.web.services.runner_control_service import RunnerControlService
from app.utils.settings import settings
from app.utils.logger import get_logger

logger = get_logger("dashboard_service")


class DashboardService:
    """Aggregates system telemetry, queue metrics, and active campaign state."""

    @classmethod
    def get_dashboard_snapshot(cls, db: Session) -> Dict[str, Any]:
        """
        Builds the complete operational dashboard snapshot.
        Guarantees fast, bounded query execution.
        """
        now = datetime.now(timezone.utc)

        # 1. Database Connectivity
        db_connected = False
        try:
            db.execute(text("SELECT 1"))
            db_connected = True
        except Exception as e:
            logger.error(f"Dashboard database check failed: {e}")

        # 2. System Health Evaluation (Phase 6 Health Engine)
        health_dto: Dict[str, Any] = {
            "state": "UNHEALTHY" if not db_connected else "STOPPED",
            "reasons": ["Database connection unavailable"] if not db_connected else [],
            "details": {},
        }
        if db_connected:
            try:
                eval_res = evaluate_system_health(db)
                health_dto = {
                    "state": eval_res.state.value,
                    "reasons": eval_res.reasons,
                    "details": eval_res.details,
                }
            except Exception as e:
                logger.error(f"Health evaluation failed: {e}")
                health_dto = {
                    "state": "UNHEALTHY",
                    "reasons": [f"Health check error: {str(e)}"],
                    "details": {},
                }

        # 3. Emergency Stop State
        e_stop_active = False
        e_stop_reason = None
        e_stop_activated_at = None
        if db_connected:
            try:
                e_stop = EmergencyStop(db)
                e_stop_active = e_stop.is_active()
                if e_stop_active:
                    last_log = (
                        db.query(AuditLog)
                        .filter(AuditLog.event_type == "EMERGENCY_STOP_ACTIVATED")
                        .order_by(AuditLog.id.desc())
                        .first()
                    )
                    if last_log:
                        e_stop_reason = last_log.error_message or "Operator triggered emergency stop"
                        e_stop_activated_at = last_log.created_at.isoformat() if last_log.created_at else None
            except Exception as e:
                logger.warning(f"Error checking emergency stop state: {e}")

        emergency_dto = {
            "is_active": e_stop_active,
            "status": "ACTIVE (HALTED)" if e_stop_active else "INACTIVE (OPERATIONAL)",
            "reason": e_stop_reason,
            "activated_at": e_stop_activated_at,
        }

        # 4. WhatsApp Provider & Session Status
        session_path = getattr(settings, "WHATSAPP_SESSION_PATH", "./data/whatsapp_session")
        profile_exists = os.path.exists(session_path) and os.path.isdir(session_path)
        has_profile_data = False
        if profile_exists:
            try:
                has_profile_data = any(Path(session_path).iterdir())
            except Exception:
                pass

        # Determine session state
        whatsapp_state = "DISCONNECTED"
        if not profile_exists:
            whatsapp_state = "DISCONNECTED"
        elif has_profile_data:
            whatsapp_state = "CONNECTED" if health_dto["state"] == "HEALTHY" else "AUTHENTICATING"

        whatsapp_dto = {
            "state": whatsapp_state,
            "profile_exists": profile_exists,
            "has_profile_data": has_profile_data,
            "disclaimer": "SEND_CONFIRMED represents WhatsApp Web UI send confirmation only, not delivery or read receipts.",
        }

        # 5. Circuit Breaker Telemetry
        tripped_campaigns: List[int] = []
        if db_connected:
            try:
                tripped_rows = (
                    db.query(Campaign.id)
                    .filter(Campaign.status == "PAUSED")
                    .all()
                )
                tripped_campaigns = [r[0] for r in tripped_rows]
            except Exception:
                pass

        cb_dto = {
            "status": "OPEN" if tripped_campaigns else "CLOSED",
            "consecutive_errors": 0,
            "threshold": 5,
            "tripped_campaigns": tripped_campaigns,
        }

        # 6. Runner Status (via RunnerControlService)
        runner_dto: Dict[str, Any] = {
            "is_running": False,
            "state": "STOPPED",
            "pid": None,
            "worker_id": None,
            "campaign_id": None,
            "started_at": None,
            "last_heartbeat": None,
            "heartbeat_age_seconds": None,
            "uptime_seconds": 0.0,
            "is_stale": False,
            "dispatches_completed": 0,
            "lock_authoritative": True,
        }
        if db_connected:
            try:
                runner_status = RunnerControlService.get_status(db)
                runner_analytics = AnalyticsService.get_runner_analytics(db)
                runner_dto.update(runner_status)
                runner_dto["dispatches_completed"] = runner_analytics.get("dispatches_completed", 0)
            except Exception as e:
                logger.warning(f"Error checking runner status: {e}")

        # 7. Active Campaign Telemetry
        active_campaign_dto: Optional[Dict[str, Any]] = None
        target_campaign_id: Optional[int] = runner_dto.get("campaign_id")

        if db_connected:
            try:
                # Look up runner campaign first, then latest RUNNING/SCHEDULED/PAUSED campaign
                campaign: Optional[Campaign] = None
                if target_campaign_id:
                    campaign = db.query(Campaign).filter(Campaign.id == target_campaign_id).first()

                if not campaign:
                    campaign = (
                        db.query(Campaign)
                        .filter(Campaign.status.in_(["RUNNING", "SCHEDULED", "PAUSED"]))
                        .order_by(Campaign.id.desc())
                        .first()
                    )

                if campaign:
                    camp_analytics = AnalyticsService.get_campaign_analytics(db, campaign.id)
                    if camp_analytics:
                        breakdown = camp_analytics.get("queue_breakdown", {})
                        perf = camp_analytics.get("performance", {})
                        contacts_bd = camp_analytics.get("contacts_breakdown", {})
                        total_con = camp_analytics.get("total_contacts", 0)

                        queued = breakdown.get("queued", 0)
                        proc = breakdown.get("processing", 0)
                        retrying = breakdown.get("retry_pending", 0)
                        remaining = queued + proc + retrying

                        active_campaign_dto = {
                            "id": campaign.id,
                            "name": campaign.name,
                            "status": campaign.status,
                            "total_contacts": total_con,
                            "eligible": contacts_bd.get("eligible", 0),
                            "queued": queued,
                            "send_confirmed": breakdown.get("confirmed_sends", 0),
                            "failed": breakdown.get("failed", 0),
                            "unknown_outcome": breakdown.get("unknown_outcome", 0),
                            "retry_pending": retrying,
                            "remaining": remaining,
                            "completion_percentage": perf.get("completion_percentage", 0.0),
                            "confirmed_send_rate": perf.get("confirmed_send_rate", 0.0),
                            "disclaimer": (
                                "SEND_CONFIRMED represents WhatsApp Web UI send confirmation only. "
                                "UNKNOWN_OUTCOME requires manual physical verification."
                            ),
                        }
            except Exception as e:
                logger.warning(f"Error fetching active campaign analytics: {e}")

        # 8. Queue Summary Telemetry
        queue_dto: Dict[str, Any] = {
            "queued": 0,
            "processing": 0,
            "retry_pending": 0,
            "confirmed_sends": 0,
            "failed": 0,
            "unknown_outcome": 0,
            "stale_leases": 0,
            "throughput_1h": 0,
            "throughput_6h": 0,
            "throughput_24h": 0,
        }
        if db_connected:
            try:
                queue_analytics = AnalyticsService.get_queue_analytics(db)
                backlog = queue_analytics.get("backlog", {})
                terminal = queue_analytics.get("terminal", {})
                tp = queue_analytics.get("confirmed_throughput", {})

                queue_dto = {
                    "queued": backlog.get("queued", 0),
                    "processing": backlog.get("processing", 0),
                    "retry_pending": backlog.get("retry_pending", 0),
                    "confirmed_sends": terminal.get("confirmed_sends", 0),
                    "failed": terminal.get("failed", 0),
                    "unknown_outcome": terminal.get("unknown_outcome", 0),
                    "stale_leases": backlog.get("stale_leases", 0),
                    "throughput_1h": tp.get("last_1_hour", 0),
                    "throughput_6h": tp.get("last_6_hours", 0),
                    "throughput_24h": tp.get("last_24_hours", 0),
                }
            except Exception as e:
                logger.warning(f"Error fetching queue analytics: {e}")

        # 9. Operational Alerts
        alerts = cls._generate_alerts(
            now=now,
            db_connected=db_connected,
            e_stop_active=e_stop_active,
            runner_dto=runner_dto,
            whatsapp_dto=whatsapp_dto,
            queue_dto=queue_dto,
            active_campaign_dto=active_campaign_dto,
            tripped_campaigns=tripped_campaigns,
        )

        return {
            "timestamp": now.isoformat(),
            "system_health": health_dto,
            "database": {
                "connected": db_connected,
                "status": "CONNECTED" if db_connected else "UNAVAILABLE",
            },
            "whatsapp": whatsapp_dto,
            "emergency_stop": emergency_dto,
            "circuit_breaker": cb_dto,
            "active_campaign": active_campaign_dto,
            "queue": queue_dto,
            "runner": runner_dto,
            "alerts": alerts,
        }

    @classmethod
    def _generate_alerts(
        cls,
        now: datetime,
        db_connected: bool,
        e_stop_active: bool,
        runner_dto: Dict[str, Any],
        whatsapp_dto: Dict[str, Any],
        queue_dto: Dict[str, Any],
        active_campaign_dto: Optional[Dict[str, Any]],
        tripped_campaigns: List[int],
    ) -> List[Dict[str, Any]]:
        """Synthesizes dynamic operational alerts based on real-time state."""
        alerts: List[Dict[str, Any]] = []
        now_iso = now.isoformat()

        # Database Alert
        if not db_connected:
            alerts.append({
                "id": "ALERT_DB_DOWN",
                "severity": "CRITICAL",
                "title": "Database Unavailable",
                "message": "SQLite database file or connection is currently unresponsive.",
                "timestamp": now_iso,
            })

        # Emergency Stop Alert
        if e_stop_active:
            alerts.append({
                "id": "ALERT_EMERGENCY_STOP",
                "severity": "CRITICAL",
                "title": "Emergency Stop Active",
                "message": "All new message queue claims are blocked system-wide. Dispatch is halted.",
                "timestamp": now_iso,
            })

        # Circuit Breaker Alert
        if tripped_campaigns:
            campaign_list = ", ".join(f"#{cid}" for cid in tripped_campaigns)
            alerts.append({
                "id": "ALERT_CB_TRIPPED",
                "severity": "CRITICAL",
                "title": "Circuit Breaker Tripped",
                "message": f"Campaign(s) {campaign_list} paused due to consecutive dispatch failure threshold breach.",
                "timestamp": now_iso,
            })

        # UNKNOWN_OUTCOME Alert
        unknown_count = queue_dto.get("unknown_outcome", 0)
        if unknown_count > 0:
            alerts.append({
                "id": "ALERT_UNKNOWN_OUTCOME",
                "severity": "HIGH",
                "title": "Unreconciled Ambiguous Dispatches",
                "message": (
                    f"{unknown_count} message(s) classified as UNKNOWN_OUTCOME. "
                    "Physical verification on WhatsApp phone is required before any manual reconciliation."
                ),
                "timestamp": now_iso,
            })

        # Stale Leases Alert
        stale_leases = queue_dto.get("stale_leases", 0)
        if stale_leases > 0:
            alerts.append({
                "id": "ALERT_STALE_LEASES",
                "severity": "HIGH",
                "title": "Stale Worker Leases Detected",
                "message": f"{stale_leases} in-flight message claim(s) exceeded the 300s timeout.",
                "timestamp": now_iso,
            })

        # Runner Health Alerts
        runner_state = runner_dto.get("state")
        if runner_state == "STALE_LOCK_DETECTED":
            alerts.append({
                "id": "ALERT_RUNNER_ORPHANED",
                "severity": "HIGH",
                "title": "Orphaned Runner Lock Detected",
                "message": "Lock file data/runner.lock belongs to a terminated process.",
                "timestamp": now_iso,
            })
        elif runner_state == "UNHEALTHY":
            heartbeat_age = runner_dto.get("heartbeat_age_seconds")
            alerts.append({
                "id": "ALERT_RUNNER_HEARTBEAT_STALE",
                "severity": "HIGH",
                "title": "Runner Heartbeat Unhealthy",
                "message": f"Production runner heartbeat is {heartbeat_age}s old (>60s threshold).",
                "timestamp": now_iso,
            })
        elif runner_state == "DEGRADED":
            heartbeat_age = runner_dto.get("heartbeat_age_seconds")
            alerts.append({
                "id": "ALERT_RUNNER_HEARTBEAT_LAGGING",
                "severity": "MEDIUM",
                "title": "Runner Heartbeat Lagging",
                "message": f"Production runner heartbeat delay is {heartbeat_age}s (>30s threshold).",
                "timestamp": now_iso,
            })
        elif runner_state == "STOPPED" and active_campaign_dto and active_campaign_dto.get("status") == "RUNNING":
            alerts.append({
                "id": "ALERT_CAMPAIGN_RUNNING_NO_RUNNER",
                "severity": "MEDIUM",
                "title": "Campaign Active Without Runner",
                "message": f"Campaign '{active_campaign_dto.get('name')}' is in RUNNING state, but no runner daemon is active.",
                "timestamp": now_iso,
            })

        # WhatsApp Session Profile Alert
        if not whatsapp_dto.get("profile_exists"):
            alerts.append({
                "id": "ALERT_WHATSAPP_PROFILE_MISSING",
                "severity": "MEDIUM",
                "title": "WhatsApp Session Profile Not Configured",
                "message": "No persistent browser profile directory was found. Initial login required.",
                "timestamp": now_iso,
            })

        return alerts
