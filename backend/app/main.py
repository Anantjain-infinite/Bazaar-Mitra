"""FastAPI entrypoint for the Bazaar Mitra Commerce API.

This is deliberately a separate app from the LiveKit voice worker
(`src/agent.py`) — the voice worker is a long-running job process with no
HTTP surface, while this is the request/response commerce API that both
the voice agent's tools and the AI Buyer will call into.

Run locally with:
    uv run uvicorn app.main:app --reload --port 8000

Route modules are registered here as each phase adds them (see
`app/api/routes/`). Nothing beyond health/readiness is wired up yet.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db.session import engine

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("bazaar_mitra")

app = FastAPI(
    title="Bazaar Mitra Commerce API",
    description=(
        "Agent-callable commerce API for Bazaar Mitra — catalog, cart, "
        "orders, Razorpay payments, policy, and audit, usable by both "
        "human-facing channels (voice, AI Buyer UI) and other AI agents."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Liveness probe — does the process respond at all."""
    return {"status": "ok", "service": "bazaar-mitra-commerce-api"}


@app.get("/ready", tags=["system"])
async def ready() -> dict:
    """Readiness probe — can we actually reach the database."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as exc:
        logger.exception("Readiness check failed")
        return {"status": "not_ready", "database": "unreachable", "error": str(exc)}


# --- Route modules are included here as each phase implements them. ---
from app.api.routes import (  # noqa: E402
    agent,
    analytics,
    audit,
    buyers,
    carts,
    catalog,
    growth,
    merchants,
    orders,
    payments,
    webhooks,
)

app.include_router(merchants.router)
app.include_router(catalog.router)
app.include_router(agent.router)
app.include_router(carts.router)
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(webhooks.router)
app.include_router(audit.router)
app.include_router(analytics.router)
app.include_router(growth.router)
app.include_router(buyers.router)
