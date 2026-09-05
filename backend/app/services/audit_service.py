"""Audit service.

The single write path for `audit_events`. Every consequential action in
the codebase (order confirmation, policy checks, payment creation,
signature verification, webhook processing) calls one of the functions
here — see the module docstrings in order_service/payment_service for
exactly where each call happens.

Design rules this module enforces:
  - Never write secrets, card numbers, CVV, or credentials into
    `metadata` or `explanation` — callers pass already-sanitized data
    (payment_service's `_sanitize_metadata` allowlist, for example), and
    this module doesn't second-guess that, but it also never accepts a
    raw external-API response dict as `metadata` without going through
    a sanitizer first (see the type hints below — `metadata` is always a
    caller-constructed dict of primitives, never "the whole payload").
  - This function itself doesn't swallow errors; callers decide whether
    an audit-write failure should block the action it's auditing.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.db.models.enums import ActorType
from app.schemas.context import ActorContext


async def record_event(
    db: AsyncSession,
    *,
    actor: ActorContext | None,
    event_type: str,
    action: str,
    explanation: str,
    success: bool,
    merchant_id: uuid.UUID | None = None,
    buyer_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    amount: Decimal | float | None = None,
    currency: str | None = None,
    policy_result: str | None = None,
    confirmation_state: str | None = None,
    failure_reason: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    """Write one audit event. Flushes but does not commit — callers
    commit as part of their own transaction, so an audit row never ends
    up persisted for an action that itself got rolled back.
    """
    actor = actor or ActorContext()
    event = AuditEvent(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        agent_name=actor.agent_name,
        session_id=actor.session_id,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        event_type=event_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        amount=Decimal(str(amount)) if amount is not None else None,
        currency=currency,
        explanation=explanation,
        policy_result=policy_result,
        confirmation_state=confirmation_state,
        success=success,
        failure_reason=failure_reason,
        metadata_=metadata or {},
    )
    db.add(event)
    await db.flush()
    return event


# --- Convenience wrappers for the specific events this codebase emits -----
# These exist so every call site constructs its audit event the same way
# (consistent event_type/action naming) rather than each caller inventing
# its own strings.


async def record_policy_check(
    db: AsyncSession,
    *,
    actor: ActorContext | None,
    merchant_id: uuid.UUID,
    buyer_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID | None,
    policy,  # PolicyCheckResult — typed loosely to avoid a service->service import cycle
) -> AuditEvent:
    return await record_event(
        db,
        actor=actor,
        event_type="policy_check",
        action="validate_transaction_policy",
        explanation=(
            "Policy check passed."
            if policy.allowed
            else f"Policy check failed: {'; '.join(policy.reasons)}"
        ),
        success=policy.allowed,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        resource_type=resource_type,
        resource_id=resource_id,
        amount=policy.amount,
        currency=policy.currency,
        policy_result="PASS" if policy.allowed else "FAIL",
        failure_reason=None if policy.allowed else "; ".join(policy.reasons),
        metadata={
            "max_transaction_amount": policy.max_transaction_amount,
            "max_daily_amount": policy.max_daily_amount,
            "daily_spend_before": policy.daily_spend_before,
            "daily_spend_after": policy.daily_spend_after,
        },
    )


async def record_order_confirmed(
    db: AsyncSession,
    *,
    actor: ActorContext | None,
    order,  # Order model
    policy,  # PolicyCheckResult
) -> AuditEvent:
    confirmed = policy.allowed
    return await record_event(
        db,
        actor=actor,
        event_type="order",
        action="confirm_order",
        explanation=(
            f"Order {order.public_order_id} confirmed by explicit user approval; policy check passed."
            if confirmed
            else f"Order {order.public_order_id} confirmation blocked: {'; '.join(policy.reasons)}"
        ),
        success=confirmed,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        resource_type="order",
        resource_id=order.id,
        amount=order.total,
        currency=order.currency,
        policy_result="PASS" if policy.allowed else "FAIL",
        confirmation_state="EXPLICIT" if confirmed else "BLOCKED_BY_POLICY",
        failure_reason=None if confirmed else "; ".join(policy.reasons),
    )


async def record_order_blocked(
    db: AsyncSession,
    *,
    actor: ActorContext | None,
    cart_id: uuid.UUID,
    merchant_id: uuid.UUID | None,
    buyer_id: uuid.UUID | None,
    reason: str,
    issues: list[str],
) -> AuditEvent:
    return await record_event(
        db,
        actor=actor,
        event_type="order",
        action="create_order",
        explanation=f"Order creation from cart blocked: {reason}",
        success=False,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        resource_type="cart",
        resource_id=cart_id,
        failure_reason="; ".join(issues) if issues else reason,
    )


async def record_payment_created(
    db: AsyncSession,
    *,
    actor: ActorContext | None,
    payment,  # Payment model
    order,  # Order model
) -> AuditEvent:
    return await record_event(
        db,
        actor=actor,
        event_type="payment",
        action="create_payment",
        explanation=(
            f"Razorpay order {payment.razorpay_order_id} created for order "
            f"{order.public_order_id}, attempt #{payment.attempt_number}, "
            "after the user explicitly approved the final order total and the policy check passed."
        ),
        success=True,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        resource_type="payment",
        resource_id=payment.id,
        amount=payment.amount,
        currency=payment.currency,
        policy_result="PASS",
        confirmation_state="EXPLICIT",
        metadata={
            "razorpay_order_id": payment.razorpay_order_id,
            "attempt_number": payment.attempt_number,
        },
    )


async def record_payment_verified(
    db: AsyncSession,
    *,
    actor: ActorContext | None,
    payment,  # Payment model
    order,  # Order model
    verified: bool,
) -> AuditEvent:
    return await record_event(
        db,
        actor=actor,
        event_type="payment",
        action="verify_payment",
        explanation=(
            f"Payment signature verified server-side for order {order.public_order_id}."
            if verified
            else f"Payment signature verification FAILED for order {order.public_order_id}."
        ),
        success=verified,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        resource_type="payment",
        resource_id=payment.id,
        amount=payment.amount,
        currency=payment.currency,
        failure_reason=None if verified else payment.failure_reason,
        metadata={"razorpay_payment_id": payment.razorpay_payment_id},
    )


async def record_webhook_processed(
    db: AsyncSession,
    *,
    event_type_from_razorpay: str,
    processed: bool,
    payment=None,  # Payment model, if matched
    order=None,  # Order model, if matched
    reason: str | None = None,
) -> AuditEvent:
    return await record_event(
        db,
        actor=ActorContext(actor_type=ActorType.WEBHOOK, agent_name="razorpay_webhook"),
        event_type="payment",
        action=f"webhook:{event_type_from_razorpay}",
        explanation=(
            f"Razorpay webhook '{event_type_from_razorpay}' processed and reconciled order/payment state."
            if processed
            else f"Razorpay webhook '{event_type_from_razorpay}' received but not processed: {reason}"
        ),
        success=processed,
        merchant_id=order.merchant_id if order else None,
        buyer_id=order.buyer_id if order else None,
        resource_type="payment",
        resource_id=payment.id if payment else None,
        amount=payment.amount if payment else None,
        currency=payment.currency if payment else None,
        failure_reason=None if processed else reason,
    )


async def list_events(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID | None = None,
    buyer_id: uuid.UUID | None = None,
    event_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    success: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent]:
    stmt = select(AuditEvent)
    if merchant_id:
        stmt = stmt.where(AuditEvent.merchant_id == merchant_id)
    if buyer_id:
        stmt = stmt.where(AuditEvent.buyer_id == buyer_id)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if resource_id:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if success is not None:
        stmt = stmt.where(AuditEvent.success == success)
    stmt = stmt.order_by(AuditEvent.timestamp.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_events_for_resource(
    db: AsyncSession, resource_id: uuid.UUID
) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.resource_id == resource_id)
        .order_by(AuditEvent.timestamp.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
