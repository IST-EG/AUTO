"""
Server-Sent Events (SSE) Telemetry Stream for Web Control Center.
"""

import json
import asyncio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.models.user import User
from app.web.dependencies import get_current_user
from app.web.services.dashboard_service import DashboardService
from app.database.connection import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("events_stream")

router = APIRouter(prefix="/api/v1/events", tags=["Events"])


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
        while True:
            if await request.is_disconnected():
                break

            db = None
            try:
                db = SessionLocal()
                snapshot = DashboardService.get_dashboard_snapshot(db)
                data_json = json.dumps(snapshot)
                yield f"event: telemetry\ndata: {data_json}\n\n"

                # Send keep-alive comment ping every 5 iterations
                iteration += 1
                if iteration % 5 == 0:
                    yield ": ping\n\n"

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in SSE stream tick: {e}")
                yield f": error {str(e)}\n\n"
            finally:
                if db is not None:
                    db.close()

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
