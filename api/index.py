"""
Vercel Serverless Entrypoint for Web Control Center.

Exposes the ASGI FastAPI application instance to the Vercel Python runtime.
Guarantees:
1. Strictly request-scoped execution (no background workers or infinite loops).
2. Clean ASGI export for serverless invocations.
"""

from app.web.app import app

# Vercel looks for 'app' in api/index.py
