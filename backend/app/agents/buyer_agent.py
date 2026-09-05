"""AI Buyer — tool functions (spec sections 8, 25, 50).

The AI Buyer is architecturally just another caller of the SAME cart/
order/policy/payment engine every other channel uses (spec section 26:
"Do not create separate business logic for each channel") — everything
below either calls catalog_service directly for cross-merchant discovery,
or delegates straight to main_agent's cart/checkout/confirm/pay tools.

Design decision worth being explicit about: `select_best_available`'s
product-selection logic is a deterministic rule (cheapest in-stock match
after real filters), not an LLM call. This is intentional, not a
shortcut — it's the same principle spec section 4 states for the whole
platform ("keep business logic out of prompts"), applied to the one
piece of "AI buyer intelligence" that actually touches money. An LLM
sitting in front of this (parsing a looser natural-language request,
narrating the comparison conversationally) is a reasonable enhancement
layer, but the purchase decision itself stays deterministic and
auditable — never a model guessing which product to buy.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import main_agent
from app.agents.context import AgentContext, save_context
from app.schemas.catalog import AgentProduct
from app.services import catalog_service


def select_best_available(products: list[AgentProduct]) -> AgentProduct | None:
    """Cheapest in-stock product from an already price-sorted list —
    see module docstring for why this is deterministic, not model-driven.
    """
    for product in products:
        if product.available:
            return product
    return None


async def discover_and_compare(
    db: AsyncSession, ctx: AgentContext, query: str, *, city: str | None = None
) -> dict:
    """Cross-merchant search + comparison + an explained selection —
    the "discover merchant(s), compare eligible products, explain
    selection" steps from spec section 8. Never fabricates price/stock;
    every candidate here is a live catalog_service read.
    """
    filters, products = await catalog_service.natural_language_search(
        db, query, merchant_id=None, limit=50, include_out_of_stock=True
    )

    if city:
        products = [p for p in products if p.merchant.city.lower() == city.lower()]

    available = [p for p in products if p.available]
    unavailable = [p for p in products if not p.available]
    best = select_best_available(products)

    explanation_parts = []
    if best:
        explanation_parts.append(
            f"{best.name} from {best.merchant.business_name} at {best.currency} {best.price} "
            f"({best.stock} in stock) is the best available option."
        )
        cheaper_but_unavailable = [
            p
            for p in unavailable
            if p.price < best.price and p.name.lower() == best.name.lower()
        ]
        for p in cheaper_but_unavailable:
            explanation_parts.append(
                f"{p.merchant.business_name} is cheaper at {p.currency} {p.price} but currently out of stock."
            )
    else:
        explanation_parts.append("No in-stock option matched the request.")

    result = {
        "ok": best is not None,
        "as_of": catalog_service.today_str(),
        "interpreted_as": filters.model_dump(),
        "candidates_considered": len(products),
        "available_count": len(available),
        "comparison": [
            {
                "product_id": str(p.id),
                "merchant_id": str(p.merchant.id),
                "merchant_name": p.merchant.business_name,
                "price": p.price,
                "stock": p.stock,
                "available": p.available,
            }
            for p in products[:10]
        ],
        "selected": (
            {
                "product_id": str(best.id),
                "merchant_id": str(best.merchant.id),
                "merchant_name": best.merchant.business_name,
                "price": best.price,
                "currency": best.currency,
            }
            if best
            else None
        ),
        "explanation": " ".join(explanation_parts),
    }
    ctx.relevant_tool_results["discover_and_compare"] = result
    await save_context(db, ctx)
    return result


async def buy_best_available(
    db: AsyncSession, ctx: AgentContext, query: str, *, city: str | None = None
) -> dict:
    """Full discover -> select -> cart -> checkout pipeline in one call,
    stopping right before payment — explicit confirmation (spec section
    17) always happens as a separate step
    (main_agent.request_payment_confirmation_and_confirm), never
    implied by the buyer's initial instruction alone, even one phrased
    as "...and buy the best available option."
    """
    comparison = await discover_and_compare(db, ctx, query, city=city)
    if not comparison["ok"]:
        return {"ok": False, "error": "no_match", "message": comparison["explanation"]}

    selected = comparison["selected"]
    merchant_result = await main_agent.select_merchant(
        db, ctx, uuid.UUID(selected["merchant_id"])
    )
    if not merchant_result["ok"]:
        return merchant_result

    add_result = await main_agent.add_to_cart(
        db, ctx, uuid.UUID(selected["product_id"]), 1
    )
    if not add_result["ok"]:
        return add_result

    checkout_result = await main_agent.checkout(db, ctx)
    return {
        "ok": checkout_result["ok"],
        "explanation": comparison["explanation"],
        "checkout": checkout_result,
    }
