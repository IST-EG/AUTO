"""
Unit tests for live operational system health evaluation.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from app.readiness.health import evaluate_system_health, HealthState, HealthResult
from app.models.app_setting import AppSetting
from app.models.message import Message
from app.queue.state_machine import QueueState
from app.runner.process_lock import ProcessLock


def test_system_health_stopped_state(db_session):
    # Base state: DB ok, E-stop inactive, no runner running
    result = evaluate_system_health(db_session)
    assert result.state == HealthState.STOPPED
    assert result.details["database_responsive"] is True
    assert result.details["emergency_stop_active"] is False
    assert result.details["runner"]["is_running"] is False
    assert any("No production runner" in r for r in result.reasons)


def test_system_health_healthy_state(db_session):
    # Mock active runner with fresh heartbeat
    mock_runner_info = {
        "is_running": True,
        "state": "RUNNING",
        "pid": 1234,
        "worker_id": "worker-1",
        "campaign_id": 1,
        "started_at": "2026-09-11T12:00:00",
        "uptime_seconds": 120.0,
        "heartbeat_age_seconds": 5.0,
        "is_stale": False,
        "dispatches_completed": 10,
    }

    with patch("app.services.analytics_service.AnalyticsService.get_runner_analytics", return_value=mock_runner_info):
        result = evaluate_system_health(db_session)
        assert result.state == HealthState.HEALTHY
        assert "runner active with fresh heartbeat" in result.reasons[0]


def test_system_health_degraded_states(db_session):
    # 1. Lagging runner heartbeat (30-60s)
    mock_lagging_runner = {
        "is_running": True,
        "state": "RUNNING",
        "pid": 1234,
        "worker_id": "worker-1",
        "campaign_id": 1,
        "started_at": "2026-09-11T12:00:00",
        "uptime_seconds": 120.0,
        "heartbeat_age_seconds": 45.0,
        "is_stale": False,
        "dispatches_completed": 10,
    }

    with patch("app.services.analytics_service.AnalyticsService.get_runner_analytics", return_value=mock_lagging_runner):
        result = evaluate_system_health(db_session)
        assert result.state == HealthState.DEGRADED
        assert any("Runner heartbeat lagging" in r for r in result.reasons)

    # 2. Unknown outcome messages exist
    mock_healthy_runner = dict(mock_lagging_runner)
    mock_healthy_runner["heartbeat_age_seconds"] = 10.0

    msg_unknown = Message(
        campaign_id=1,
        contact_id=1,
        rendered_content="Test",
        status="FAILED",
        error_type="UNKNOWN_OUTCOME",
        idempotency_key="health_unknown_1",
    )
    db_session.add(msg_unknown)
    db_session.commit()

    with patch("app.services.analytics_service.AnalyticsService.get_runner_analytics", return_value=mock_healthy_runner):
        result = evaluate_system_health(db_session)
        assert result.state == HealthState.DEGRADED
        assert any("UNKNOWN_OUTCOME" in r for r in result.reasons)


def test_system_health_emergency_stop_state(db_session):
    setting = db_session.query(AppSetting).filter(AppSetting.key == "emergency_stop").first()
    if not setting:
        setting = AppSetting(key="emergency_stop", value=json.dumps({"active": True, "reason": "Severe Issue"}))
        db_session.add(setting)
    else:
        setting.value = json.dumps({"active": True, "reason": "Severe Issue"})
    db_session.commit()

    result = evaluate_system_health(db_session)
    assert result.state == HealthState.STOPPED
    assert any("Emergency stop engaged" in r for r in result.reasons)

    # Reset
    setting.value = json.dumps({"active": False})
    db_session.commit()


def test_system_health_unhealthy_database_failure():
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("SQLite Database Locked / Corrupted")

    result = evaluate_system_health(mock_db)
    assert result.state == HealthState.UNHEALTHY
    assert result.details["database_responsive"] is False
    assert any("Database unresponsive" in r for r in result.reasons)
