"""
CLI commands package.
"""

from app.cli.commands.session import (
    handle_session_login,
    handle_session_status,
    handle_session_logout,
)
from app.cli.commands.campaign import (
    handle_campaign_run,
    handle_campaign_status,
    handle_campaign_pause,
    handle_campaign_resume,
    handle_campaign_stop,
)
from app.cli.commands.queue import (
    handle_queue_status,
    handle_queue_inspect,
    handle_queue_reconcile,
    handle_queue_override,
)
from app.cli.commands.emergency import (
    handle_emergency_stop,
    handle_emergency_status,
    handle_emergency_resume,
)
from app.cli.commands.runner import (
    handle_runner_start,
    handle_runner_status,
    handle_runner_stop,
)
from app.cli.commands.analytics import (
    handle_analytics_campaign,
    handle_analytics_queue,
    handle_analytics_runner,
    handle_analytics_provider,
    handle_analytics_system,
)
from app.cli.commands.preflight import handle_preflight
from app.cli.commands.health import handle_system_health

__all__ = [
    "handle_session_login",
    "handle_session_status",
    "handle_session_logout",
    "handle_campaign_run",
    "handle_campaign_status",
    "handle_campaign_pause",
    "handle_campaign_resume",
    "handle_campaign_stop",
    "handle_queue_status",
    "handle_queue_inspect",
    "handle_queue_reconcile",
    "handle_queue_override",
    "handle_emergency_stop",
    "handle_emergency_status",
    "handle_emergency_resume",
    "handle_runner_start",
    "handle_runner_status",
    "handle_runner_stop",
    "handle_analytics_campaign",
    "handle_analytics_queue",
    "handle_analytics_runner",
    "handle_analytics_provider",
    "handle_analytics_system",
    "handle_preflight",
    "handle_system_health",
]
