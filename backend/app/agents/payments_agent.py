"""Payments Specialist — tool functions (spec section 10A).

Handles payment status, failure explanations, and retry. Deliberately
CANNOT mark a payment successful — that only ever happens through
payment_service's signature verification + webhook reconciliation, which
this specialist doesn't bypass any more than the main agent does.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import AgentContext, save_context
from app.services import handoff_service, order_service, payment_service


async def get_payment_status(
    db: AsyncSession, ctx: AgentContext, payment_id: uuid.UUID | None = None
) -> dict:
    pid = payment_id or ctx.payment_id
    if pid is None:
        return {"ok": False, "error": "No payment to check"}
    payment = await payment_service.get_payment(db, pid)
    if payment is None:
        return {"ok": False, "error": f"No payment found with id {pid}"}
    return {
        "ok": True,
        "payment_id": str(payment.id),
        "attempt_number": payment.attempt_number,
        "status": payment.status.value
        if hasattr(payment.status, "value")
        else payment.status,
        "amount": float(payment.amount),
        "currency": payment.currency,
        "failure_code": payment.failure_code,
        "failure_reason": payment.failure_reason,
    }


async def get_payment_history(
    db: AsyncSession, ctx: AgentContext, order_id: uuid.UUID | None = None
) -> dict:
    """Every attempt for this order, oldest first — the "Attempt #1
    FAILED, Attempt #2 CAPTURED" story from spec section 16.
    """
    oid = order_id or ctx.order_id
    if oid is None:
        return {"ok": False, "error": "No order to check"}
    order = await order_service.get_order(db, oid)
    if order is None:
        return {"ok": False, "error": f"No order found with id {oid}"}
    attempts = sorted(order.payments, key=lambda p: p.attempt_number)
    return {
        "ok": True,
        "public_order_id": order.public_order_id,
        "attempts": [
            {
                "attempt_number": p.attempt_number,
                "status": p.status.value if hasattr(p.status, "value") else p.status,
                "failure_reason": p.failure_reason,
            }
            for p in attempts
        ],
    }


async def retry_payment(db: AsyncSession, ctx: AgentContext) -> dict:
    """Retry after a failed attempt — creates a fresh Razorpay order,
    never reuses or force-succeeds the failed one.
    """
    if ctx.order_id is None:
        return {"ok": False, "error": "No order to retry payment for"}

    try:
        payment = await payment_service.create_payment_attempt(
            db, ctx.order_id, actor=ctx.to_actor("payments_agent")
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
        "attempt_number": payment.attempt_number,
        "razorpay_order_id": payment.razorpay_order_id,
        "razorpay_key_id": settings.razorpay_key_id,
        "amount_paise": rupees_to_paise(payment.amount),
        "currency": payment.currency,
    }


async def return_to_main_agent(
    db: AsyncSession, ctx: AgentContext, reason: str = "payment issue resolved"
) -> dict:
    handoff = await handoff_service.return_to_previous_agent(
        db, ctx.session_id, reason=reason
    )
    await db.commit()
    ctx.current_agent = handoff.to_agent
    ctx.previous_agent = handoff.from_agent
    return {"ok": True, "returned_to": handoff.to_agent}
