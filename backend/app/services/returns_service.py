"""Returns & refunds service — DB-backed, replacing the illustrative
`backend/src/returns_policy.py` for anything routed through the new
commerce backend (per spec section 3: the existing return policy is
explicitly "hand-built/illustrative" and should move behind a real
service).

Policy is intentionally a simple, documented constant for now (a single
global return window) rather than a database table — the spec's minimum
schema doesn't include a per-merchant policy table for returns (only
`returns`/`refunds` for tracking actual requests), so inventing one
would be scope creep. Making this configurable per merchant is a
natural extension if/when that's needed; the window is centralized
here as one constant specifically so that future change is a one-line
edit, not a search-and-replace.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Refund, Return
from app.db.models.enums import OrderStatus, ReturnStatus
from app.schemas.context import ActorContext
from app.services import audit_service, order_service

RETURN_WINDOW_DAYS = 7
POLICY_LAST_UPDATED = "2026-01-01"


def get_return_policy() -> dict:
    return {
        "window_days": RETURN_WINDOW_DAYS,
        "eligible_order_statuses": [
            OrderStatus.PAID.value,
            OrderStatus.FULFILLED.value,
        ],
        "refund_method": "Original payment method via Razorpay",
        "as_of": POLICY_LAST_UPDATED,
    }


async def check_return_eligibility(
    db: AsyncSession, *, order_id: uuid.UUID, product_id: uuid.UUID
) -> dict:
    order = await order_service.get_order(db, order_id)
    if order is None:
        return {"eligible": False, "reason": f"No order found with id {order_id}"}

    item = next((i for i in order.items if i.product_id == product_id), None)
    if item is None:
        return {"eligible": False, "reason": "This product is not part of that order"}

    if order.status not in (OrderStatus.PAID, OrderStatus.FULFILLED):
        return {
            "eligible": False,
            "reason": f"Order is {order.status}, not eligible for return (must be PAID or FULFILLED)",
        }

    if order.confirmed_at is None:
        return {"eligible": False, "reason": "Order has no confirmation date on file"}

    days_since_purchase = (datetime.now(UTC) - order.confirmed_at).days
    if days_since_purchase > RETURN_WINDOW_DAYS:
        return {
            "eligible": False,
            "reason": (
                f"{days_since_purchase} days since purchase exceeds the "
                f"{RETURN_WINDOW_DAYS}-day return window"
            ),
            "days_since_purchase": days_since_purchase,
            "window_days": RETURN_WINDOW_DAYS,
        }

    stmt = select(Return).where(
        Return.order_id == order_id, Return.product_id == product_id
    )
    existing = (await db.execute(stmt)).scalars().all()
    already_requested = any(r.status != ReturnStatus.REJECTED for r in existing)
    if already_requested:
        return {
            "eligible": False,
            "reason": "A return request already exists for this item",
        }

    return {
        "eligible": True,
        "reason": "Within the return window and order is eligible",
        "days_since_purchase": days_since_purchase,
        "window_days": RETURN_WINDOW_DAYS,
        "item_name": item.name_snapshot,
    }


async def create_return_request(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    product_id: uuid.UUID,
    reason: str,
    actor: ActorContext | None = None,
) -> Return:
    eligibility = await check_return_eligibility(
        db, order_id=order_id, product_id=product_id
    )
    order = await order_service.get_order(db, order_id)

    return_request = Return(
        order_id=order_id,
        product_id=product_id,
        reason=reason,
        status=ReturnStatus.REQUESTED
        if eligibility["eligible"]
        else ReturnStatus.REJECTED,
        requested_at=datetime.now(UTC),
        resolved_at=None if eligibility["eligible"] else datetime.now(UTC),
    )
    db.add(return_request)
    await db.flush()

    order_ref = order.public_order_id if order else str(order_id)
    await audit_service.record_event(
        db,
        actor=actor,
        event_type="return",
        action="create_return_request",
        explanation=f"Return request created for order {order_ref}: {eligibility['reason']}",
        success=eligibility["eligible"],
        merchant_id=order.merchant_id if order else None,
        buyer_id=order.buyer_id if order else None,
        resource_type="return",
        resource_id=return_request.id,
        failure_reason=None if eligibility["eligible"] else eligibility["reason"],
    )

    return return_request


async def get_return(db: AsyncSession, return_id: uuid.UUID) -> Return | None:
    return await db.get(Return, return_id)


async def get_refund_status(db: AsyncSession, *, order_id: uuid.UUID) -> Refund | None:
    """Most recent refund for an order, if any. Refunds in this schema
    are keyed by order (and payment), not by a specific return request —
    see module docstring for why.
    """
    stmt = (
        select(Refund)
        .where(Refund.order_id == order_id)
        .order_by(Refund.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().first()
