"""
Tests for Server-Sent Events (SSE) telemetry stream endpoint.

Verifies:
- 401 Unauthorized for unauthenticated requests
- Proper SSE headers (Content-Type: text/event-stream, X-Accel-Buffering: no)
- Initial telemetry payload streaming
"""

import pytest
import json
from unittest.mock import patch

from app.models.user import UserRole
from app.web.config import web_settings
from app.web.security.session import session_manager


def test_events_stream_unauthenticated(client):
    resp = client.get("/api/v1/events/stream")
    assert resp.status_code == 401


def test_events_stream_authenticated(client, web_session, create_user):
    user = create_user("sse_user", UserRole.VIEWER)
    token = session_manager.create_session(web_session, user)

    # Patch asyncio.sleep so the generator doesn't hang in test client
    with patch("asyncio.sleep") as mock_sleep:
        # Cause asyncio.sleep to raise GeneratorExit or StopIteration after first yield
        mock_sleep.side_effect = GeneratorExit

        resp = client.get(
            "/api/v1/events/stream",
            cookies={web_settings.WEB_SESSION_COOKIE_NAME: token}
        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert resp.headers.get("x-accel-buffering") == "no"

        # Verify initial telemetry event was received
        content = resp.text
        assert "event: telemetry" in content
        assert "data: " in content
        assert '"system_health":' in content
        assert '"queue":' in content
