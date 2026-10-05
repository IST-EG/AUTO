"""
Server-Sent Events (SSE) Telemetry Stream for Web Control Center.

Delivers real-time operational telemetry with:
1. Non-blocking thread-offloaded database access (preserves asyncio event loop).
2. Lightweight telemetry snapshot (avoids full 39-query dashboard aggregations).
3. State fingerprinting (avoids serializing duplicate payloads when idle).
4. Strictly backwards-compatible event format.
"""

import json
import asyncio
from typing import Dict, Any
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.models.user import User
from app.web.dependencies import get_current_user
from app.web.services.dashboard_service import DashboardService
from app.database.connection import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("events_stream")

router = APIRouter(prefix="/api/v1/events", tags=["Events"])


from starlette.concurrency import run_in_threadpool

def _fetch_telemetry_blocking() -> Dict[str, Any]:
    """Runs lightweight telemetry snapshot in worker thread off the main event loop."""
    db = SessionLocal()
    try:
        return DashboardService.get_telemetry_snapshot(db)
    finally:
        db.close()


@router.get("/stream")
async def event_stream(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """
    Server-Sent Events (SSE) endpoint providing real-time telemetry updates.
    Authenticated access only. Emits telemetry events every 3 seconds.
    """
    async def sse_generator():
        # Handshake event
        yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"

        iteration = 0
        last_fingerprint = None

        while True:
            if await request.is_disconnected():
                break

            try:
                # Offload DB I/O to threadpool to keep asyncio event loop responsive (Python 3.8+)
                snapshot = await run_in_threadpool(_fetch_telemetry_blocking)

                # State fingerprint to detect meaningful state changes
                fp_data = (
                    snapshot.get("system_health", {}).get("state"),
                    snapshot.get("emergency_stop", {}).get("is_active"),
                    snapshot.get("runner", {}).get("state"),
                    snapshot.get("runner", {}).get("pid"),
                    snapshot.get("whatsapp", {}).get("state"),
                    snapshot.get("queue", {}).get("queued"),
                    snapshot.get("queue", {}).get("processing"),
                )
                curr_fingerprint = hash(fp_data)
                state_changed = (curr_fingerprint != last_fingerprint)

                iteration += 1

                # Emit full telemetry payload on state change, initial connection, or every 4th cycle (12s refresh)
                if state_changed or (iteration % 4 == 0) or (iteration == 1):
                    last_fingerprint = curr_fingerprint
                    data_json = json.dumps(snapshot)
                    yield f"event: telemetry\ndata: {data_json}\n\n"
                else:
                    # Lightweight keep-alive comment when state is unchanged
                    yield ": ping\n\n"

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in SSE stream tick: {e}")
                yield f": error {str(e)}\n\n"

            try:
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Essential for Nginx reverse proxy compatibility
        }
    )
