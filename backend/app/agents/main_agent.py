"""Main Commerce Agent — tool functions (spec section 9 + 32).

Every function here is a plain async function taking `(db, ctx, ...)` and
returning a JSON-serializable dict — intentionally framework-agnostic so
the same tool works whether it's wrapped as a LiveKit `@function_tool`
(voice), an LLM tool-call handler (AI Buyer, Phase 6), or called directly
from a test. None of these functions ever:
  - invent a price, stock number, or order id (everything is read from
    the DB via the Phase 1-3 services),
  - mark a payment successful without server-side verification,
  - bypass the policy engine.

Each tool that changes cart/order/payment state updates `ctx` in place
AND persists it via `save_context` — callers don't need to remember to
do both.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import AgentContext, save_context
from app.db.models.enums import CartStatus
from app.services import (
    cart_service,
    catalog_service,
    handoff_service,
    order_service,
    payment_service,
    recommendation_service,
)


async def search_products(db: AsyncSession, ctx: AgentContext, query: str) -> dict:
    """Natural-language product search, optionally scoped to the
    session's currently-selected merchant. Never fabricates price/stock
    — see catalog_service.
    """
    filters, products = await catalog_service.natural_language_search(
        db, query, merchant_id=ctx.merchant_id
    )
    result = {
        "as_of": catalog_service.today_str(),
        "interpreted_as": filters.model_dump(),
        "count": len(products),
        "products": [p.model_dump(mode="json") for p in products[:10]],
    }
    ctx.relevant_tool_results["search_products"] = result
    await save_context(db, ctx)
    return result


async def select_merchant(
    db: AsyncSession, ctx: AgentContext, merchant_id: uuid.UUID
) -> dict:
    merchant = await catalog_service.get_merchant(db, merchant_id)
    if merchant is None:
        return {"ok": False, "error": f"No merchant found with id {merchant_id}"}
    ctx.merchant_id = merchant.id
    await save_context(db, ctx)
    return {
        "ok": True,
        "merchant": {"id": str(merchant.id), "business_name": merchant.business_name},
    }


async def _ensure_cart(db: AsyncSession, ctx: AgentContext) -> uuid.UUID:
    if ctx.cart_id:
        cart = await cart_service.get_cart(db, ctx.cart_id)
        # Direct comparison (not `.value`) on purpose — CartStatus is a
        # str-subclassed enum, so this works whether `cart.status` is the
        # enum member (a freshly-constructed, not-yet-round-tripped
        # object) or a plain str (after a real DB round trip, since the
        # column type is a bare String) — see cart_service for the same
        # established pattern.
        if cart is not None and cart.status == CartStatus.OPEN:
            return cart.id
    if ctx.merchant_id is None or ctx.buyer_id is None:
        raise ValueError("A merchant and buyer must be set before starting a cart")
    cart = await cart_service.create_cart(db, ctx.merchant_id, ctx.buyer_id)
    ctx.cart_id = cart.id
    await save_context(db, ctx)
    return cart.id


async def add_to_cart(
    db: AsyncSession, ctx: AgentContext, product_id: uuid.UUID, quantity: int = 1
) -> dict:
    cart_id = await _ensure_cart(db, ctx)
    cart = await cart_service.get_cart(db, cart_id)
    item = await cart_service.add_item(db, cart, product_id, quantity)

    # If this product was just shown as a recommendation, adding it to
    # the cart IS the buyer's acceptance — see get_recommendations.
    accepted_recommendation_id = None
    pending = ctx.relevant_tool_results.get("pending_recommendations", {})
    rec_id = pending.pop(str(product_id), None)
    if rec_id:
        await recommendation_service.mark_accepted(db, uuid.UUID(rec_id))
        accepted_recommendation_id = rec_id
        ctx.relevant_tool_results["pending_recommendations"] = pending

    await db.commit()
    cart = await cart_service.get_cart(db, cart_id)
    if accepted_recommendation_id:
        await save_context(db, ctx)
    return {
        "ok": True,
        "cart_id": str(cart.id),
        "added": {
            "product_id": str(item.product_id),
            "quantity": item.quantity,
            "unit_price": float(item.unit_price),
        },
        "cart_total": float(cart.total),
        "currency": cart.currency,
        "accepted_recommendation_id": accepted_recommendation_id,
    }


async def view_cart(db: AsyncSession, ctx: AgentContext) -> dict:
    if ctx.cart_id is None:
        return {"ok": True, "empty": True, "items": [], "total": 0.0}
    cart = await cart_service.get_cart(db, ctx.cart_id)
    if cart is None:
        return {"ok": True, "empty": True, "items": [], "total": 0.0}
    return {
        "ok": True,
        "empty": len(cart.items) == 0,
        "items": [
            {
                "product_id": str(i.product_id),
                "quantity": i.quantity,
                "unit_price": float(i.unit_price),
                "line_total": float(i.line_total),
            }
            for i in cart.items
        ],
        "subtotal": float(cart.subtotal),
        "total": float(cart.total),
        "currency": cart.currency,
    }


async def get_recommendations(
    db: AsyncSession, ctx: AgentContext, product_id: uuid.UUID
) -> dict:
    """Cross-sell/upsell suggestions for a product already in view.
    Every suggestion returned here is also recorded as a "shown"
    recommendation (recommendation_service) and cached on the session
    keyed by product id, so a subsequent add_to_cart for that exact
    product can be recognized as an *acceptance* — see add_to_cart.
    Nothing here changes the cart on its own (spec section 21).
    """
    product = await catalog_service.get_product_by_id(db, product_id)
    if product is None:
        return {"ok": False, "error": f"No product found with id {product_id}"}
    agent_product = await catalog_service.to_agent_product(db, product)

    pending = dict(ctx.relevant_tool_results.get("pending_recommendations", {}))
    cross_sell, upsell = [], []
    for ref in agent_product.cross_sell_products:
        rec = await recommendation_service.create_recommendation(
            db,
            merchant_id=product.merchant_id,
            product_id=ref.id,
            source_product_id=product.id,
            recommendation_type="cross_sell",
            rationale=ref.reason or "Commonly purchased alongside this item",
            source_signal="product_relationship",
            confidence=0.8,
            cart_id=ctx.cart_id,
        )
        pending[str(ref.id)] = str(rec.id)
        cross_sell.append({**ref.model_dump(), "recommendation_id": str(rec.id)})
    for ref in agent_product.upsell_products:
        rec = await recommendation_service.create_recommendation(
            db,
            merchant_id=product.merchant_id,
            product_id=ref.id,
            source_product_id=product.id,
            recommendation_type="upsell",
            rationale=ref.reason or "A higher-value alternative buyers often prefer",
            source_signal="product_relationship",
            confidence=0.6,
            cart_id=ctx.cart_id,
        )
        pending[str(ref.id)] = str(rec.id)
        upsell.append({**ref.model_dump(), "recommendation_id": str(rec.id)})

    ctx.relevant_tool_results["pending_recommendations"] = pending
    await db.commit()
    await save_context(db, ctx)

    return {"ok": True, "cross_sell": cross_sell, "upsell": upsell}


async def checkout(
    db: AsyncSession, ctx: AgentContext, acknowledge_price_change: bool = False
) -> dict:
    """Cart -> Order, with the policy result included so the caller can
    present the order summary + policy status before asking for
    explicit confirmation (spec section 17). Does NOT confirm the order
    — see request_payment_confirmation_and_confirm.
    """
    if ctx.cart_id is None:
        return {"ok": False, "error": "No cart to check out — add items first"}

    try:
        order = await order_service.create_order_from_cart(
            db,
            ctx.cart_id,
            acknowledge_price_change=acknowledge_price_change,
            actor=ctx.to_actor(),
        )
    except order_service.PriceOrStockChangedError as exc:
        await db.commit()
        return {
            "ok": False,
            "error": "price_changed",
            "message": "Prices changed since being added to the cart — show the buyer the new total and "
            "retry with acknowledge_price_change=true only after they explicitly re-confirm.",
            "issues": [i.model_dump(mode="json") for i in exc.issues],
        }
    except order_service.OutOfStockError as exc:
        await db.commit()
        return {
            "ok": False,
            "error": "out_of_stock",
            "message": "One or more items are no longer available in the requested quantity.",
            "issues": [i.model_dump(mode="json") for i in exc.issues],
        }
    except order_service.EmptyCartError:
        return {"ok": False, "error": "empty_cart", "message": "The cart has no items."}

    await db.commit()
    from app.services import policy_service

    policy = await policy_service.validate_transaction(
        db,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        amount=order.total,
        currency=order.currency,
    )
    ctx.order_id = order.id
    await save_context(db, ctx)

    return {
        "ok": True,
        "order_id": str(order.id),
        "public_order_id": order.public_order_id,
        "total": float(order.total),
        "currency": order.currency,
        "policy": policy.model_dump(),
        "requires_explicit_confirmation": True,
    }


async def request_payment_confirmation_and_confirm(
    db: AsyncSession, ctx: AgentContext
) -> dict:
    """The explicit-confirmation step (spec section 17). Only call this
    after the buyer has clearly said yes to the exact total shown by
    `checkout`. Runs the policy engine one final time server-side —
    never proceeds on a policy failure, no matter what the caller passes.
    """
    if ctx.order_id is None:
        return {"ok": False, "error": "No order to confirm — call checkout first"}

    try:
        order, policy = await order_service.confirm_order(
            db, ctx.order_id, actor=ctx.to_actor()
        )
    except order_service.OutOfStockError as exc:
        await db.commit()
        return {
            "ok": False,
            "error": "out_of_stock",
            "issues": [i.model_dump(mode="json") for i in exc.issues],
        }
    await db.commit()

    if not policy.allowed:
        return {
            "ok": False,
            "error": "policy_rejected",
            "message": "This order cannot be confirmed — it doesn't pass the merchant's policy checks.",
            "policy": policy.model_dump(),
        }

    return {
        "ok": True,
        "order_id": str(order.id),
        "public_order_id": order.public_order_id,
        "status": order.status.value
        if hasattr(order.status, "value")
        else order.status,
        "confirmed_at": order.confirmed_at.isoformat() if order.confirmed_at else None,
    }


async def initiate_payment(db: AsyncSession, ctx: AgentContext) -> dict:
    """Create the Razorpay order for a CONFIRMED order. Returns exactly
    what a checkout UI needs (razorpay_order_id, key id, amount) — never
    claims the payment succeeded, since nothing about creating a Razorpay
    order means money has moved yet.
    """
    if ctx.order_id is None:
        return {"ok": False, "error": "No confirmed order to pay for"}

    try:
        payment = await payment_service.create_payment_attempt(
            db, ctx.order_id, actor=ctx.to_actor()
        )
    except payment_service.OrderNotPayableError as exc:
        return {"ok": False, "error": "order_not_payable", "message": str(exc)}
    except payment_service.PolicyRejectedError as exc:
        await db.commit()
        return {
            "ok": False,
            "error": "policy_rejected",
            "policy": exc.policy.model_dump(),
        }
    except payment_service.PaymentGatewayUnavailableError:
        await db.commit()
        return {
            "ok": False,
            "error": "gateway_unavailable",
            "message": "The payment service is temporarily unavailable. Your order has not been charged.",
        }

    await db.commit()
    ctx.payment_id = payment.id
    await save_context(db, ctx)

    from app.config import get_settings
    from app.integrations.razorpay import rupees_to_paise

    settings = get_settings()
    return {
        "ok": True,
        "payment_id": str(payment.id),
        "razorpay_order_id": payment.razorpay_order_id,
        "razorpay_key_id": settings.razorpay_key_id,
        "amount_paise": rupees_to_paise(payment.amount),
        "currency": payment.currency,
    }


async def get_order_status(
    db: AsyncSession, ctx: AgentContext, order_id: uuid.UUID | None = None
) -> dict:
    oid = order_id or ctx.order_id
    if oid is None:
        return {"ok": False, "error": "No order to check"}
    order = await order_service.get_order(db, oid)
    if order is None:
        return {"ok": False, "error": f"No order found with id {oid}"}
    return {
        "ok": True,
        "public_order_id": order.public_order_id,
        "status": order.status.value
        if hasattr(order.status, "value")
        else order.status,
        "payment_status": order.payment_status,
        "total": float(order.total),
        "currency": order.currency,
    }


# --- Handoffs ---------------------------------------------------------


async def _handoff(
    db: AsyncSession, ctx: AgentContext, *, to_agent: str, reason: str, summary: str
) -> dict:
    try:
        handoff = await handoff_service.initiate_handoff(
            db,
            ctx.session_id,
            to_agent=to_agent,
            reason=reason,
            conversation_summary=summary,
        )
    except handoff_service.HandoffFailedError as exc:
        await db.commit()
        return {
            "ok": False,
            "error": "handoff_failed",
            "message": f"Couldn't connect to {exc.to_agent} right now: {exc.reason}. "
            "Continue helping with what's available instead of leaving the user stuck.",
        }
    await db.commit()
    ctx.current_agent = to_agent
    ctx.previous_agent = handoff.from_agent
    return {"ok": True, "handed_off_to": to_agent, "from_agent": handoff.from_agent}


async def handoff_to_payments(db: AsyncSession, ctx: AgentContext, reason: str) -> dict:
    return await _handoff(
        db,
        ctx,
        to_agent="payments_agent",
        reason=reason,
        summary=f"Order {ctx.order_id}, payment {ctx.payment_id}. Reason: {reason}",
    )


async def handoff_to_returns(db: AsyncSession, ctx: AgentContext, reason: str) -> dict:
    return await _handoff(
        db,
        ctx,
        to_agent="returns_agent",
        reason=reason,
        summary=f"Order {ctx.order_id}. Reason: {reason}",
    )


async def handoff_to_order_support(
    db: AsyncSession, ctx: AgentContext, reason: str
) -> dict:
    return await _handoff(
        db,
        ctx,
        to_agent="order_support_agent",
        reason=reason,
        summary=f"Order {ctx.order_id}. Reason: {reason}",
    )


async def handoff_to_growth(db: AsyncSession, ctx: AgentContext, reason: str) -> dict:
    """Buyer-facing sessions can never reach the (merchant-facing)
    growth agent — this deliberately demonstrates the handoff-failure
    path from spec section 12 until/unless this session has merchant
    context (see handoff_service.MERCHANT_ONLY_AGENTS).
    """
    return await _handoff(
        db, ctx, to_agent="growth_agent", reason=reason, summary=reason
    )
