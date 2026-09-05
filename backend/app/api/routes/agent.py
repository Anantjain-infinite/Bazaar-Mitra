from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.agent_session import (
    AgentAddToCartRequest,
    AgentBuyBestAvailableRequest,
    AgentCheckoutRequest,
    AgentSearchRequest,
    AgentSessionOut,
    CreateAgentSessionRequest,
)
from app.schemas.catalog import (
    AgentCatalogResponse,
    NaturalLanguageSearchRequest,
    NaturalLanguageSearchResponse,
)
from app.services import catalog_service, session_service

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.get("/catalog", response_model=AgentCatalogResponse)
async def agent_catalog(
    merchant_id: uuid.UUID | None = Query(
        default=None, description="Restrict to one merchant"
    ),
    category: str | None = Query(default=None),
    max_price: float | None = Query(default=None, ge=0),
    min_price: float | None = Query(default=None, ge=0),
    in_stock_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> AgentCatalogResponse:
    """The primary discovery endpoint for AI agents: catalog products
    across ALL active merchants, in the agent-readable shape (price,
    stock, availability, related/upsell/cross-sell — see AgentProduct).

    Every filter here is applied as a real SQL WHERE clause — an agent
    narrowing by max_price or in_stock_only gets back only rows that
    actually satisfy it, straight from the database.
    """
    products = await catalog_service.get_agent_catalog(
        db,
        merchant_id=merchant_id,
        category=category,
        max_price=max_price,
        min_price=min_price,
        in_stock_only=in_stock_only,
        limit=limit,
    )
    return AgentCatalogResponse(
        as_of=catalog_service.today_str(), count=len(products), products=products
    )


@router.post("/search", response_model=NaturalLanguageSearchResponse)
async def agent_search(
    body: NaturalLanguageSearchRequest, db: AsyncSession = Depends(get_db)
) -> NaturalLanguageSearchResponse:
    """Natural-language product search across merchants, e.g.
    `{"query": "wireless mouse under ₹1000 that is in stock"}`.

    The query is parsed deterministically into structured filters
    (see catalog_service.parse_natural_language_query) — the
    `interpreted_as` field in the response shows exactly how it was
    understood, so a calling agent (or a human debugging it) never has
    to trust a black box.
    """
    filters, products = await catalog_service.natural_language_search(
        db, body.query, merchant_id=body.merchant_id, limit=body.limit
    )
    return NaturalLanguageSearchResponse(
        as_of=catalog_service.today_str(),
        interpreted_as=filters,
        count=len(products),
        products=products,
    )


# --- Agent-session-aware endpoints ---------------------------------------
# These expose the Phase 5 tool layer (app.agents.main_agent / buyer_agent)
# directly over HTTP, so any external AI agent — not just the in-process
# voice worker — can drive the full search -> cart -> checkout -> confirm
# -> pay flow through one consistent session, without managing cart_id/
# order_id bookkeeping itself. Internally these call the exact same
# services (cart_service, order_service, policy_service, payment_service)
# as every other channel — see spec section 26: "Do not create separate
# business logic for each channel."

from app.agents import buyer_agent, main_agent  # noqa: E402
from app.agents.context import AgentContext, load_context  # noqa: E402


def _session_out(ctx: AgentContext) -> AgentSessionOut:
    return AgentSessionOut(
        session_id=ctx.session_id,
        current_agent=ctx.current_agent,
        previous_agent=ctx.previous_agent,
        merchant_id=ctx.merchant_id,
        buyer_id=ctx.buyer_id,
        cart_id=ctx.cart_id,
        order_id=ctx.order_id,
        payment_id=ctx.payment_id,
        language=ctx.language,
    )


async def _load_ctx_or_404(db: AsyncSession, session_id: uuid.UUID) -> AgentContext:
    try:
        return await load_context(db, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions", response_model=AgentSessionOut, status_code=201)
async def create_agent_session(
    body: CreateAgentSessionRequest, db: AsyncSession = Depends(get_db)
) -> AgentSessionOut:
    session = await session_service.create_session(
        db,
        buyer_id=body.buyer_id,
        merchant_id=body.merchant_id,
        channel=body.channel,
        language=body.language,
    )
    await db.commit()
    ctx = await load_context(db, session.id)
    return _session_out(ctx)


@router.get("/sessions/{session_id}", response_model=AgentSessionOut)
async def get_agent_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> AgentSessionOut:
    ctx = await _load_ctx_or_404(db, session_id)
    return _session_out(ctx)


@router.post("/sessions/{session_id}/search")
async def agent_session_search(
    session_id: uuid.UUID, body: AgentSearchRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    ctx = await _load_ctx_or_404(db, session_id)
    return await main_agent.search_products(db, ctx, body.query)


@router.post("/cart")
async def agent_add_to_cart(
    *,
    session_id: uuid.UUID = Query(...),
    body: AgentAddToCartRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Add an item to the session's cart (spec section 6: POST /api/v1/agent/cart)."""
    ctx = await _load_ctx_or_404(db, session_id)
    return await main_agent.add_to_cart(db, ctx, body.product_id, body.quantity)


@router.get("/cart")
async def agent_view_cart(
    session_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)
) -> dict:
    ctx = await _load_ctx_or_404(db, session_id)
    return await main_agent.view_cart(db, ctx)


@router.post("/checkout")
async def agent_checkout(
    *,
    session_id: uuid.UUID = Query(...),
    body: AgentCheckoutRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Cart -> Order with policy result (spec section 6: POST /api/v1/agent/checkout)."""
    ctx = await _load_ctx_or_404(db, session_id)
    return await main_agent.checkout(
        db, ctx, acknowledge_price_change=body.acknowledge_price_change
    )


@router.post("/confirm")
async def agent_confirm(
    session_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)
) -> dict:
    """The explicit-confirmation step (spec section 6: POST /api/v1/agent/confirm).
    Only call this after the caller has shown the buyer the exact total
    from /checkout and gotten a clear yes.
    """
    ctx = await _load_ctx_or_404(db, session_id)
    return await main_agent.request_payment_confirmation_and_confirm(db, ctx)


@router.post("/pay")
async def agent_pay(
    session_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)
) -> dict:
    ctx = await _load_ctx_or_404(db, session_id)
    return await main_agent.initiate_payment(db, ctx)


@router.post("/buy-best-available")
async def agent_buy_best_available(
    *,
    session_id: uuid.UUID = Query(...),
    body: AgentBuyBestAvailableRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The AI Buyer's one-call discover -> compare -> select -> cart ->
    checkout pipeline (spec section 8). Stops before payment — the
    caller must still call /confirm and /pay after showing the buyer
    the selection and total. Never skips explicit confirmation, even
    for a request phrased as "...and buy the best available option."
    """
    ctx = await _load_ctx_or_404(db, session_id)
    return await buyer_agent.buy_best_available(db, ctx, body.query, city=body.city)


@router.get("/capabilities")
async def agent_capabilities() -> dict:
    """Machine-readable capability description (spec section 48) — makes
    Bazaar Mitra discoverable and understandable by another AI agent
    without needing to read this codebase first.
    """
    return {
        "platform": "Bazaar Mitra",
        "currencies_supported": ["INR"],
        "catalog": {
            "discovery": "GET /api/v1/agent/catalog",
            "search": "POST /api/v1/agent/search (natural language)",
            "never_fabricates": ["price", "stock", "availability", "discounts"],
        },
        "buying_flow": {
            "steps": [
                "search",
                "add_to_cart",
                "checkout",
                "confirm",
                "pay",
                "verify (webhook-reconciled)",
            ],
            "session_endpoints": {
                "create_session": "POST /api/v1/agent/sessions",
                "cart": "POST/GET /api/v1/agent/cart?session_id=...",
                "checkout": "POST /api/v1/agent/checkout?session_id=...",
                "confirm": "POST /api/v1/agent/confirm?session_id=...",
                "pay": "POST /api/v1/agent/pay?session_id=...",
                "buy_best_available": "POST /api/v1/agent/buy-best-available?session_id=...",
            },
            "confirmation_required": True,
            "confirmation_examples": ["yes", "confirm", "proceed", "haan", "हाँ"],
        },
        "payment_methods": "All Razorpay Standard Checkout methods (card, UPI, netbanking, wallet)",
        "order_lifecycle": [
            "DRAFT",
            "PENDING_CONFIRMATION",
            "CONFIRMED",
            "PAYMENT_PENDING",
            "PAID",
            "PAYMENT_FAILED",
            "FULFILLED",
            "CANCELLED",
            "REFUND_PENDING",
            "REFUNDED",
        ],
        "specialist_agents": [
            "main_agent",
            "payments_agent",
            "returns_agent",
            "order_support_agent",
            "growth_agent",
        ],
        "transaction_limits": "Enforced server-side per merchant/buyer — see GET /api/v1/merchants/{id} and policy responses on checkout/confirm.",
        "audit_trail": "GET /api/v1/audit, GET /api/v1/audit/{resource_id}",
    }
