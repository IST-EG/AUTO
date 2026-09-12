"""
Web Control Center Middleware and Centralized Exception Handlers.

Enforces production security headers, correlation IDs, and standardized
error responses without leaking internal stack traces, passwords, or tokens.
"""

import uuid
from typing import Callable
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.web.config import web_settings
from app.web.schemas.common import APIResponse
from app.utils.logger import get_logger

logger = get_logger("web_api")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds hardened security headers to all HTTP responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Set correlation ID for request tracing
        correlation_id = request.headers.get("X-Correlation-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.correlation_id = correlation_id

        response: Response = await call_next(request)

        # Apply OWASP Security Headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';"
        )
        response.headers["X-Correlation-ID"] = correlation_id

        # HSTS in production environments
        if web_settings.ENVIRONMENT.lower() == "production" or web_settings.WEB_COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

        return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """Standardized handler for HTTPExceptions."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    # If the request accepts HTML and is a UI route, let it render or redirect
    if "text/html" in request.headers.get("Accept", "") and not request.url.path.startswith("/api/"):
        # UI route handling
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return Response(status_code=302, headers={"Location": "/login"})

    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "UNPROCESSABLE_ENTITY",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE"
    }
    error_code = code_map.get(exc.status_code, "ERROR")

    return JSONResponse(
        status_code=exc.status_code,
        content=APIResponse.fail(
            code=error_code,
            message=str(exc.detail),
            meta={"correlation_id": correlation_id}
        ).model_dump()
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    """Handler for Pydantic validation errors that sanitizes sensitive field values."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    # Sanitize validation error details to never leak passwords
    sanitized_errors = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field_name = str(loc[-1]) if loc else "unknown"
        if "password" in field_name.lower():
            sanitized_errors.append({
                "field": field_name,
                "issue": "Password does not meet required criteria"
            })
        else:
            sanitized_errors.append({
                "field": field_name,
                "issue": err.get("msg", "Invalid value")
            })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=APIResponse.fail(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            details=sanitized_errors,
            meta={"correlation_id": correlation_id}
        ).model_dump()
    )


async def generic_exception_handler(request: Request, exc: Exception) -> Response:
    """Fallback handler for unhandled server exceptions (500)."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    logger.error(
        f"Unhandled server exception on {request.method} {request.url.path}: {str(exc)}",
        exc_info=True
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=APIResponse.fail(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred. Please contact support.",
            meta={"correlation_id": correlation_id}
        ).model_dump()
    )
