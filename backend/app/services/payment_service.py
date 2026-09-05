"""Payment service.

State machine this module enforces (see spec sections 15-16):

    CONFIRMED --create_payment_attempt--> PAYMENT_PENDING (Payment: CREATED)
    PAYMENT_PENDING --verify_payment (signature OK)--> still PAYMENT_PENDING (Payment: AUTHORIZED)
    PAYMENT_PENDING --webhook payment.captured--> PAID (Payment: CAPTURED)
    PAYMENT_PENDING --verify_payment (signature BAD)--> PAYMENT_FAILED (Payment: FAILED)
    PAYMENT_PENDING --webhook payment.failed--> PAYMENT_FAILED (Payment: FAILED)
    PAYMENT_FAILED --create_payment_attempt (retry)--> PAYMENT_PENDING (new Payment row, attempt_number+1)

The critical design decision here, straight from the spec: a verified
client-side signature is necessary but NOT sufficient to mark an order
PAID. It proves the payment response wasn't tampered with in transit,
so we advance the *Payment* row to AUTHORIZED — but the *Order* only
becomes PAID when the `payment.captured` webhook arrives and its
signature verifies too. Two independent server-side confirmations,
never a client callback alone. "Order state must not become PAID
merely because the browser says payment succeeded" — this module is
where that rule lives.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment
from app.db.models.enums import OrderStatus, PaymentStatus
from app.integrations import razorpay as razorpay_integration
from app.schemas.context import ActorContext
from app.services import (
    audit_service,
    order_service,
    policy_service,
    recommendation_service,
)

# Fields we're willing to persist from a Razorpay payment/order entity.
# Deliberately an allowlist, not a blind dict copy — Razorpay's API could
# add fields in the future, and an allowlist means a new field is simply
# ignored by default rather than potentially leaking something sensitive
# into raw_response_metadata / audit logs. None of these fields can ever
# contain a full card number or CVV — Razorpay itself never returns those.
_SAFE_METADATA_FIELDS = {
    "method",
    "bank",
    "wallet",
    "vpa",
    "status",
    "card_id",
    "international",
    "amount",
    "currency",
    "error_code",
    "error_description",
    "error_source",
    "error_step",
    "error_reason",
}


def _sanitize_metadata(entity: dict[str, Any]) -> dict[str, Any]:
    sanitized = {k: v for k, v in entity.items() if k in _SAFE_METADATA_FIELDS}
    card = entity.get("card")
    if isinstance(card, dict):
        # Only ever the network/last4, never the full number.
        sanitized["card"] = {
            k: v for k, v in card.items() if k in {"network", "last4", "type", "issuer"}
        }
    return sanitized


class PaymentError(Exception):
    """Base class for payment errors the API layer translates to HTTP responses."""


class PaymentGatewayUnavailableError(PaymentError):
    pass


class OrderNotPayableError(PaymentError):
    pass


class PolicyRejectedError(PaymentError):
    def __init__(self, policy: policy_service.PolicyCheckResult):
        self.policy = policy
        super().__init__("Policy check failed at payment time")


class InvalidSignatureError(PaymentError):
    pass


class InvalidWebhookSignatureError(PaymentError):
    pass


async def get_payment(db: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    return await db.get(Payment, payment_id)


async def _next_attempt_number(db: AsyncSession, order_id: uuid.UUID) -> int:
    stmt = (
        select(Payment.attempt_number)
        .where(Payment.order_id == order_id)
        .order_by(Payment.attempt_number.desc())
    )
    result = await db.execute(stmt)
    latest = result.scalars().first()
    return (latest or 0) + 1


async def create_payment_attempt(
    db: AsyncSession,
    order_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
    actor: ActorContext | None = None,
) -> Payment:
    """Create a fresh Razorpay order + Payment row for this order. Used
    for both the very first attempt (order must be CONFIRMED) and every
    retry (order must be PAYMENT_FAILED) — the state machine only cares
    that the order isn't already paid/paying/terminal, not which route
    called this.
    """
    if idempotency_key:
        stmt = select(Payment).where(Payment.idempotency_key == idempotency_key)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

    order = await order_service.get_order(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=404, detail=f"No order found with id {order_id}"
        )

    if order.status not in (
        OrderStatus.CONFIRMED,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.PAYMENT_PENDING,
    ):
        # Not audited — this is a usage/state error (calling create-payment
        # on a non-payable order), not a blocked financial action; nothing
        # was attempted with Razorpay or the policy engine.
        raise OrderNotPayableError(
            f"Order {order.public_order_id} is {order.status} — payment can only be attempted "
            "for a CONFIRMED order, or retried after a PAYMENT_FAILED attempt."
        )

    # Defense in depth: re-run the policy check one more time, right
    # before money actually moves. Confirmation could have happened a
    # while ago; today's spend or the limits themselves may have changed.
    policy = await policy_service.validate_transaction(
        db,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        amount=order.total,
        currency=order.currency,
    )
    await audit_service.record_policy_check(
        db,
        actor=actor,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        resource_type="order",
        resource_id=order.id,
        policy=policy,
    )
    if not policy.allowed:
        # The route rolls back on this exception — commit now so the
        # audit record of *why* payment was blocked survives that
        # rollback. See order_service.create_order_from_cart for the
        # same pattern with the same reasoning.
        await db.commit()
        raise PolicyRejectedError(policy)

    try:
        razorpay_order = razorpay_integration.create_order(
            amount_rupees=Decimal(str(order.total)),
            currency=order.currency,
            receipt=order.public_order_id,
            notes={
                "bazaar_mitra_order_id": str(order.id),
                "public_order_id": order.public_order_id,
            },
        )
    except razorpay_integration.RazorpayNotConfiguredError:
        raise
    except Exception as exc:
        # No Payment row is written for a pure connectivity/gateway
        # failure — nothing was attempted with Razorpay, so there's
        # nothing to record as an "attempt". The order stays exactly as
        # it was, ready to retry. Still audited (as a failure against
        # the order, not a payment) so there's a record that a payment
        # was attempted and the gateway was unreachable.
        await audit_service.record_event(
            db,
            actor=actor,
            event_type="payment",
            action="create_payment",
            explanation=f"Payment gateway unavailable when creating a Razorpay order for {order.public_order_id}.",
            success=False,
            merchant_id=order.merchant_id,
            buyer_id=order.buyer_id,
            resource_type="order",
            resource_id=order.id,
            amount=order.total,
            currency=order.currency,
            failure_reason=str(exc),
        )
        await db.commit()  # same reasoning as the policy-rejected branch above
        raise PaymentGatewayUnavailableError(str(exc)) from exc

    attempt_number = await _next_attempt_number(db, order.id)
    payment = Payment(
        order_id=order.id,
        attempt_number=attempt_number,
        razorpay_order_id=razorpay_order["id"],
        amount=order.total,
        currency=order.currency,
        status=PaymentStatus.CREATED,
        raw_response_metadata=_sanitize_metadata(razorpay_order),
        idempotency_key=idempotency_key,
    )
    db.add(payment)

    order.status = OrderStatus.PAYMENT_PENDING
    order.payment_status = PaymentStatus.CREATED.value
    await db.flush()
    await audit_service.record_payment_created(
        db, actor=actor, payment=payment, order=order
    )

    return payment


async def verify_payment(
    db: AsyncSession,
    payment_id: uuid.UUID,
    *,
    razorpay_payment_id: str,
    razorpay_signature: str,
    actor: ActorContext | None = None,
) -> Payment:
    """Server-side verification of the signature Razorpay Checkout
    returned to the frontend. This does NOT mark the order PAID — see
    module docstring. It marks this specific Payment attempt AUTHORIZED
    (signature genuinely came from Razorpay) and waits for webhook
    reconciliation to close the loop.
    """
    payment = await get_payment(db, payment_id)
    if payment is None:
        raise HTTPException(
            status_code=404, detail=f"No payment found with id {payment_id}"
        )
    if payment.razorpay_order_id is None:
        raise OrderNotPayableError(f"Payment {payment_id} was never sent to Razorpay")

    valid = razorpay_integration.verify_payment_signature(
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=razorpay_signature,
    )

    order = await order_service.get_order(db, payment.order_id)

    if not valid:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = "SIGNATURE_VERIFICATION_FAILED"
        payment.failure_reason = (
            "The payment signature could not be verified server-side."
        )
        if order is not None and order.status == OrderStatus.PAYMENT_PENDING:
            order.status = OrderStatus.PAYMENT_FAILED
            order.payment_status = PaymentStatus.FAILED.value
        await db.flush()
        if order is not None:
            await audit_service.record_payment_verified(
                db, actor=actor, payment=payment, order=order, verified=False
            )
        raise InvalidSignatureError("Signature verification failed")

    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.status = PaymentStatus.AUTHORIZED
    payment.verified_at = datetime.now(UTC)
    if order is not None:
        order.payment_status = PaymentStatus.AUTHORIZED.value
    await db.flush()
    if order is not None:
        await audit_service.record_payment_verified(
            db, actor=actor, payment=payment, order=order, verified=True
        )

    return payment


async def _find_payment_for_webhook_entity(
    db: AsyncSession, entity: dict[str, Any]
) -> Payment | None:
    razorpay_payment_id = entity.get("id")
    razorpay_order_id = entity.get("order_id")

    if razorpay_payment_id:
        stmt = select(Payment).where(Payment.razorpay_payment_id == razorpay_payment_id)
        payment = (await db.execute(stmt)).scalar_one_or_none()
        if payment is not None:
            return payment

    if razorpay_order_id:
        stmt = (
            select(Payment)
            .where(Payment.razorpay_order_id == razorpay_order_id)
            .order_by(Payment.attempt_number.desc())
        )
        return (await db.execute(stmt)).scalars().first()

    return None


async def handle_webhook_event(
    db: AsyncSession, *, raw_body: bytes, signature: str
) -> dict[str, Any]:
    """Process a Razorpay webhook. Verifies the signature against the RAW
    body BEFORE trusting anything in it — an unverified webhook payload
    is exactly as trustworthy as an anonymous POST from the internet,
    because that's what it is until proven otherwise.
    """
    if not razorpay_integration.verify_webhook_signature(
        raw_body=raw_body, signature=signature
    ):
        raise InvalidWebhookSignatureError("Webhook signature verification failed")

    event = json.loads(raw_body)
    event_type = event.get("event", "")
    payload = event.get("payload", {})

    if event_type == "payment.captured":
        entity = payload.get("payment", {}).get("entity", {})
        payment = await _find_payment_for_webhook_entity(db, entity)
        if payment is None:
            return {"processed": False, "reason": "no matching payment found"}
        if payment.status == PaymentStatus.CAPTURED:
            return {
                "processed": True,
                "idempotent_noop": True,
            }  # already handled a duplicate delivery

        payment.status = PaymentStatus.CAPTURED
        if entity.get("id"):
            payment.razorpay_payment_id = entity["id"]
        if payment.verified_at is None:
            payment.verified_at = datetime.now(UTC)
        payment.raw_response_metadata = {
            **payment.raw_response_metadata,
            **_sanitize_metadata(entity),
        }

        order = await order_service.get_order(db, payment.order_id)
        if order is not None:
            latest_attempt = await _next_attempt_number(db, order.id) - 1
            if (
                payment.attempt_number == latest_attempt
            ):  # never let a stale attempt regress current state
                order.status = OrderStatus.PAID
                order.payment_status = PaymentStatus.CAPTURED.value
                await db.flush()
                await recommendation_service.mark_converted_for_order(db, order)
        await db.flush()
        await audit_service.record_webhook_processed(
            db,
            event_type_from_razorpay=event_type,
            processed=True,
            payment=payment,
            order=order,
        )
        return {"processed": True}

    if event_type == "payment.failed":
        entity = payload.get("payment", {}).get("entity", {})
        payment = await _find_payment_for_webhook_entity(db, entity)
        if payment is None:
            return {"processed": False, "reason": "no matching payment found"}
        if payment.status == PaymentStatus.FAILED:
            return {"processed": True, "idempotent_noop": True}

        payment.status = PaymentStatus.FAILED
        payment.failure_code = entity.get("error_code")
        payment.failure_reason = entity.get("error_description")
        payment.raw_response_metadata = {
            **payment.raw_response_metadata,
            **_sanitize_metadata(entity),
        }

        order = await order_service.get_order(db, payment.order_id)
        if order is not None:
            latest_attempt = await _next_attempt_number(db, order.id) - 1
            if (
                payment.attempt_number == latest_attempt
                and order.status == OrderStatus.PAYMENT_PENDING
            ):
                order.status = OrderStatus.PAYMENT_FAILED
                order.payment_status = PaymentStatus.FAILED.value
        await db.flush()
        await audit_service.record_webhook_processed(
            db,
            event_type_from_razorpay=event_type,
            processed=True,
            payment=payment,
            order=order,
        )
        return {"processed": True}

    if event_type == "order.paid":
        entity = payload.get("order", {}).get("entity", {})
        razorpay_order_id = entity.get("id")
        if not razorpay_order_id:
            return {"processed": False, "reason": "no order id in payload"}
        stmt = (
            select(Payment)
            .where(Payment.razorpay_order_id == razorpay_order_id)
            .order_by(Payment.attempt_number.desc())
        )
        payment = (await db.execute(stmt)).scalars().first()
        if payment is None:
            return {"processed": False, "reason": "no matching payment found"}
        order = await order_service.get_order(db, payment.order_id)
        if order is not None and order.status != OrderStatus.PAID:
            order.status = OrderStatus.PAID
            order.payment_status = PaymentStatus.CAPTURED.value
            await db.flush()
            await recommendation_service.mark_converted_for_order(db, order)
        await db.flush()
        await audit_service.record_webhook_processed(
            db,
            event_type_from_razorpay=event_type,
            processed=True,
            payment=payment,
            order=order,
        )
        return {"processed": True}

    # Unrecognized event types are acknowledged (2xx) but ignored — we
    # only subscribe to the three above in the Razorpay dashboard, but
    # being tolerant of unknown event types keeps this endpoint safe if
    # more are enabled later without a code change.
    return {"processed": False, "reason": f"unhandled event type: {event_type}"}
