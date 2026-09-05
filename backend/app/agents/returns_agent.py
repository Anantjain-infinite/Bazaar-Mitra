"""Returns & Refunds Specialist — tool functions (spec section 10B).

Backed by returns_service, which validates real database state (order
status, confirmation date, existing return requests) rather than letting
the LLM guess eligibility.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import AgentContext
from app.services import handoff_service, returns_service


async def get_return_policy(db: AsyncSession, ctx: AgentContext) -> dict:
    return {"ok": True, **returns_service.get_return_policy()}


async def check_return_eligibility(
    db: AsyncSession, ctx: AgentContext, product_id: uuid.UUID
) -> dict:
    if ctx.order_id is None:
        return {
            "ok": False,
            "error": "No order on file for this session to check a return against",
        }
    result = await returns_service.check_return_eligibility(
        db, order_id=ctx.order_id, product_id=product_id
    )
    return {"ok": True, **result}


async def create_return_request(
    db: AsyncSession, ctx: AgentContext, product_id: uuid.UUID, reason: str
) -> dict:
    if ctx.order_id is None:
        return {
            "ok": False,
            "error": "No order on file for this session to return an item from",
        }
    return_request = await returns_service.create_return_request(
        db,
        order_id=ctx.order_id,
        product_id=product_id,
        reason=reason,
        actor=ctx.to_actor("returns_agent"),
    )
    await db.commit()
    return {
        "ok": True,
        "return_id": str(return_request.id),
        "status": return_request.status.value
        if hasattr(return_request.status, "value")
        else return_request.status,
        "eligible": return_request.status != "REJECTED",
    }


async def get_refund_status(db: AsyncSession, ctx: AgentContext) -> dict:
    if ctx.order_id is None:
        return {"ok": False, "error": "No order on file for this session"}
    refund = await returns_service.get_refund_status(db, order_id=ctx.order_id)
    if refund is None:
        return {"ok": True, "found": False}
    return {
        "ok": True,
        "found": True,
        "status": refund.status.value
        if hasattr(refund.status, "value")
        else refund.status,
        "amount": float(refund.amount),
        "processed_at": refund.processed_at.isoformat()
        if refund.processed_at
        else None,
    }


async def return_to_main_agent(
    db: AsyncSession, ctx: AgentContext, reason: str = "return request handled"
) -> dict:
    handoff = await handoff_service.return_to_previous_agent(
        db, ctx.session_id, reason=reason
    )
    await db.commit()
    ctx.current_agent = handoff.to_agent
    ctx.previous_agent = handoff.from_agent
    return {"ok": True, "returned_to": handoff.to_agent}
