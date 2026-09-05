"""Order Support Specialist — tool functions (spec section 10C).

Order status, cancellation (only before payment — see
order_service.cancel_order), and a placeholder for delivery/fulfillment
status until real fulfillment tracking exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import AgentContext
from app.services import handoff_service, order_service


async def get_order_status(
    db: AsyncSession, ctx: AgentContext, order_id: uuid.UUID | None = None
) -> dict:
    oid = order_id or ctx.order_id
    if oid is None:
        return {"ok": False, "error": "No order to check"}
    order = await order_service.get_order(db, oid)
    if order is None:
        return {"ok": False, "found": False, "error": f"No order found with id {oid}"}
    return {
        "ok": True,
        "found": True,
        "public_order_id": order.public_order_id,
        "status": order.status.value
        if hasattr(order.status, "value")
        else order.status,
        "payment_status": order.payment_status,
        "total": float(order.total),
        "currency": order.currency,
        "item_count": len(order.items),
    }


async def cancel_order(db: AsyncSession, ctx: AgentContext, reason: str) -> dict:
    if ctx.order_id is None:
        return {"ok": False, "error": "No order to cancel"}
    try:
        order = await order_service.cancel_order(
            db, ctx.order_id, reason=reason, actor=ctx.to_actor("order_support_agent")
        )
    except order_service.OrderNotCancellableError as exc:
        return {"ok": False, "error": "not_cancellable", "message": str(exc)}
    await db.commit()
    return {
        "ok": True,
        "public_order_id": order.public_order_id,
        "status": order.status.value,
    }


async def get_fulfillment_status(db: AsyncSession, ctx: AgentContext) -> dict:
    """No dedicated fulfillment/shipping tracking exists in this schema
    yet — this is an honest proxy off order.status rather than a
    fabricated delivery estimate (spec section 31 forbids inventing
    delivery dates).
    """
    if ctx.order_id is None:
        return {"ok": False, "error": "No order to check"}
    order = await order_service.get_order(db, ctx.order_id)
    if order is None:
        return {"ok": False, "error": "Order not found"}
    status = order.status.value if hasattr(order.status, "value") else order.status
    return {
        "ok": True,
        "public_order_id": order.public_order_id,
        "order_status": status,
        "note": "No dedicated delivery/shipment tracking is available yet — this reflects order status only.",
    }


async def return_to_main_agent(
    db: AsyncSession, ctx: AgentContext, reason: str = "order support request handled"
) -> dict:
    handoff = await handoff_service.return_to_previous_agent(
        db, ctx.session_id, reason=reason
    )
    await db.commit()
    ctx.current_agent = handoff.to_agent
    ctx.previous_agent = handoff.from_agent
    return {"ok": True, "returned_to": handoff.to_agent}
