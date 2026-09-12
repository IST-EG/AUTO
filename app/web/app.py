"""
FastAPI Application Factory for Web Control Center.

Configures routing, static asset mounting, security middleware, and centralized
exception handling.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.database import SessionLocal
from app.web.config import web_settings
from app.web.middleware import (
    SecurityHeadersMiddleware,
    http_exception_handler,
    validation_exception_handler,
    generic_exception_handler
)
from app.web.security.session import session_manager
from app.web.routes.api.setup import router as setup_api_router
from app.web.routes.api.auth import router as auth_api_router
from app.web.routes.api.health import router as health_api_router
from app.web.routes.api.dashboard import router as dashboard_api_router
from app.web.routes.api.runner import router as runner_api_router
from app.web.routes.api.system import router as system_api_router
from app.web.routes.api.events import router as events_api_router
from app.web.routes.api.templates import router as templates_api_router
from app.web.routes.api.campaigns import router as campaigns_api_router
from app.web.routes.api.contacts import router as contacts_api_router
from app.web.routes.api.queue import router as queue_api_router
from app.web.routes.ui.views import router as ui_views_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context for startup and shutdown routines."""
    # Startup: Clean up expired sessions
    try:
        with SessionLocal() as db:
            session_manager.cleanup_expired_sessions(db)
    except Exception:
        pass

    yield

    # Shutdown routines if needed


def create_app() -> FastAPI:
    """Creates and configures the FastAPI Web Control Center application."""
    app = FastAPI(
        title="Integra Outreach Web Control Center",
        description="Operational Control Center for WhatsApp Outreach Automation",
        version="7.2.0",
        debug=web_settings.DEBUG,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json"
    )

    # 1. Mount Static Files
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 2. Add Reverse Proxy, Trusted Hosts & Security Headers Middleware
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    if web_settings.ALLOWED_HOSTS and web_settings.ALLOWED_HOSTS.strip() != "*":
        allowed_hosts = [h.strip() for h in web_settings.ALLOWED_HOSTS.split(",") if h.strip()]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    app.add_middleware(SecurityHeadersMiddleware)

    # 3. Register Centralized Exception Handlers
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # 4. Include Routers
    app.include_router(health_api_router)
    app.include_router(setup_api_router)
    app.include_router(auth_api_router)
    app.include_router(dashboard_api_router)
    app.include_router(runner_api_router)
    app.include_router(system_api_router)
    app.include_router(events_api_router)
    app.include_router(templates_api_router)
    app.include_router(campaigns_api_router)
    app.include_router(contacts_api_router)
    app.include_router(queue_api_router)
    app.include_router(ui_views_router)

    return app


# Default ASGI application instance
app = create_app()
