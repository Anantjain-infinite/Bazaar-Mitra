"""Order service.

This module owns the two safety-critical transitions in the buying flow:

  1. Cart -> Order: re-validates every item's price and stock against the
     live product row before anything is written. A drifted price blocks
     order creation unless the caller explicitly acknowledges it (which
     should only ever happen after the buyer has seen the new total and
     re-confirmed — that responsibility lives in the calling agent/route,
     not here). A stock shortfall always blocks, full stop — there is no
     "acknowledge" override for stock, because there's nothing to buy.

  2. Order -> Confirmed: the explicit-confirmation gate. This is where
     the deterministic policy engine (policy_service) gets the final say
     — an order can be *created* even if it would exceed policy (so there
     is a durable record of what was requested), but it can never be
     *confirmed* while the policy check fails.

Nothing in this module trusts a price, stock number, or "the user said
yes" claim that didn't come from a fresh database read or an explicit
caller-supplied flag on this exact call.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Order, OrderItem, Product
from app.db.models.enums import CartStatus, OrderStatus
from app.schemas.context import ActorContext
from app.schemas.order import OrderIssue
from app.services import audit_service, cart_service, policy_service


class OrderError(Exception):
    """Base class for order errors the API layer translates to HTTP responses."""


class PriceOrStockChangedError(OrderError):
    def __init__(self, issues: list[OrderIssue]):
        self.issues = issues
        super().__init__(
            f"{len(issues)} item(s) changed since they were added to the cart"
        )


class OutOfStockError(OrderError):
    def __init__(self, issues: list[OrderIssue]):
        self.issues = issues
        super().__init__(
            f"{len(issues)} item(s) are no longer available in the requested quantity"
        )


class EmptyCartError(OrderError):
    pass


def generate_public_order_id() -> str:
    return "ORD-" + secrets.token_hex(4).upper()


async def get_order(db: AsyncSession, order_id: uuid.UUID) -> Order | None:
    stmt = select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_orders(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID | None = None,
    buyer_id: uuid.UUID | None = None,
    status: OrderStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Order]:
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
    )
    if merchant_id:
        stmt = stmt.where(Order.merchant_id == merchant_id)
    if buyer_id:
        stmt = stmt.where(Order.buyer_id == buyer_id)
    if status:
        stmt = stmt.where(Order.status == status)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_order_by_cart(db: AsyncSession, cart_id: uuid.UUID) -> Order | None:
    stmt = (
        select(Order).where(Order.cart_id == cart_id).options(selectinload(Order.items))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_order_from_cart(
    db: AsyncSession,
    cart_id: uuid.UUID,
    *,
    acknowledge_price_change: bool = False,
    actor: ActorContext | None = None,
) -> Order:
    cart = await cart_service.get_cart(db, cart_id)
    if cart is None:
        raise HTTPException(status_code=404, detail=f"No cart found with id {cart_id}")

    # Idempotency: this cart was already converted — hand back the
    # existing order instead of creating a duplicate. Handles retried
    # requests (double-tap, flaky network) safely.
    if cart.status == CartStatus.CONVERTED:
        existing = await _get_order_by_cart(db, cart_id)
        if existing is not None:
            return existing

    if cart.status != CartStatus.OPEN:
        raise HTTPException(
            status_code=409,
            detail=f"Cart {cart_id} is {cart.status}, can't be checked out",
        )
    if not cart.items:
        raise EmptyCartError(f"Cart {cart_id} has no items")

    # --- Price + stock integrity check against live product rows ---
    price_issues: list[OrderIssue] = []
    stock_issues: list[OrderIssue] = []
    live_products: dict[uuid.UUID, Product] = {}

    for item in cart.items:
        product = await db.get(Product, item.product_id)
        if product is None or not product.active:
            stock_issues.append(
                OrderIssue(
                    product_id=item.product_id,
                    product_name="(unknown)",
                    issue="product_unavailable",
                    message="This product is no longer available.",
                )
            )
            continue
        live_products[item.product_id] = product

        if product.price != item.unit_price:
            price_issues.append(
                OrderIssue(
                    product_id=product.id,
                    product_name=product.name,
                    issue="price_changed",
                    quoted_price=float(item.unit_price),
                    current_price=float(product.price),
                    message=(
                        f"Price for {product.name} changed from "
                        f"{cart.currency} {item.unit_price} to {cart.currency} {product.price}."
                    ),
                )
            )
        if item.quantity > product.stock_quantity:
            stock_issues.append(
                OrderIssue(
                    product_id=product.id,
                    product_name=product.name,
                    issue="out_of_stock",
                    requested_quantity=item.quantity,
                    available_stock=product.stock_quantity,
                    message=(
                        f"Only {product.stock_quantity} of {product.name} in stock, "
                        f"{item.quantity} requested."
                    ),
                )
            )

    # Stock problems are never overridable — nothing to acknowledge into existing.
    if stock_issues:
        await audit_service.record_order_blocked(
            db,
            actor=actor,
            cart_id=cart.id,
            merchant_id=cart.merchant_id,
            buyer_id=cart.buyer_id,
            reason="one or more items out of stock",
            issues=[i.message for i in stock_issues],
        )
        # Committed immediately (not left for the caller's commit): the
        # route rolls back the session on this exception, which would
        # otherwise silently discard the very audit record explaining
        # why the order was blocked. Nothing else is pending at this
        # point in the flow, so this can't accidentally persist a
        # half-built order.
        await db.commit()
        raise OutOfStockError(stock_issues)

    # Price drift blocks unless the caller explicitly acknowledges it —
    # and when acknowledged, we re-quote the cart items to the live price
    # right now, so the order is built from the price the buyer actually
    # just re-confirmed, never the stale one.
    if price_issues and not acknowledge_price_change:
        await audit_service.record_order_blocked(
            db,
            actor=actor,
            cart_id=cart.id,
            merchant_id=cart.merchant_id,
            buyer_id=cart.buyer_id,
            reason="one or more prices changed since being quoted",
            issues=[i.message for i in price_issues],
        )
        await db.commit()  # see comment above — same reasoning
        raise PriceOrStockChangedError(price_issues)

    if price_issues and acknowledge_price_change:
        for item in cart.items:
            product = live_products.get(item.product_id)
            if product is not None and product.price != item.unit_price:
                item.unit_price = product.price
                item.line_total = product.price * item.quantity
        await db.flush()
        cart_service.recompute_cart_totals(cart)
        await db.flush()

    # --- Build the order, snapshotting everything ---
    order = Order(
        public_order_id=generate_public_order_id(),
        merchant_id=cart.merchant_id,
        buyer_id=cart.buyer_id,
        cart_id=cart.id,
        status=OrderStatus.PENDING_CONFIRMATION,
        payment_status="NONE",
        currency=cart.currency,
        subtotal=cart.subtotal,
        discount=cart.discount,
        shipping_amount=Decimal("0"),
        total=cart.total,
        confirmation_required=True,
        confirmation_received=False,
    )
    db.add(order)
    await db.flush()

    for item in cart.items:
        product = live_products[item.product_id]
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                sku=product.sku,
                name_snapshot=product.name,
                quantity=item.quantity,
                quoted_unit_price=item.unit_price,
                final_unit_price=item.unit_price,  # equal at creation time; see module docstring
                total=item.line_total,
            )
        )

    cart.status = CartStatus.CONVERTED
    await db.flush()

    return await get_order(db, order.id)


async def confirm_order(
    db: AsyncSession, order_id: uuid.UUID, *, actor: ActorContext | None = None
) -> tuple[Order, policy_service.PolicyCheckResult]:
    order = await get_order(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=404, detail=f"No order found with id {order_id}"
        )

    # Idempotent: confirming an already-confirmed order just returns the
    # current state rather than erroring — safe to retry. Not audited as
    # a new event since nothing changed; the original confirm already was.
    if order.status in (
        OrderStatus.CONFIRMED,
        OrderStatus.PAYMENT_PENDING,
        OrderStatus.PAID,
    ):
        policy = await policy_service.validate_transaction(
            db,
            merchant_id=order.merchant_id,
            buyer_id=order.buyer_id,
            amount=order.total,
            currency=order.currency,
        )
        return order, policy

    if order.status != OrderStatus.PENDING_CONFIRMATION:
        raise HTTPException(
            status_code=409,
            detail=f"Order {order.public_order_id} is {order.status}, cannot be confirmed",
        )

    # Defensive re-check of stock at confirmation time too — a window
    # exists between order creation and confirmation where stock could
    # have moved. Price is NOT re-checked here: it was already locked at
    # order-creation time (final_unit_price), and that's the number the
    # policy check below and the eventual payment will use.
    stock_issues: list[OrderIssue] = []
    for item in order.items:
        product = await db.get(Product, item.product_id)
        if (
            product is None
            or not product.active
            or product.stock_quantity < item.quantity
        ):
            stock_issues.append(
                OrderIssue(
                    product_id=item.product_id,
                    product_name=item.name_snapshot,
                    issue="out_of_stock",
                    requested_quantity=item.quantity,
                    available_stock=product.stock_quantity if product else 0,
                    message=f"{item.name_snapshot} is no longer available in the requested quantity.",
                )
            )
    if stock_issues:
        await audit_service.record_order_blocked(
            db,
            actor=actor,
            cart_id=order.cart_id,
            merchant_id=order.merchant_id,
            buyer_id=order.buyer_id,
            reason="one or more items out of stock at confirmation time",
            issues=[i.message for i in stock_issues],
        )
        await (
            db.commit()
        )  # survive the route's rollback on this exception — see create_order_from_cart
        raise OutOfStockError(stock_issues)

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
        # Order stays PENDING_CONFIRMATION — nothing here silently
        # proceeds past a failed policy check. Not raised as an
        # exception, so the caller's normal commit path persists this
        # audit event along with the unchanged order state.
        await audit_service.record_order_confirmed(
            db, actor=actor, order=order, policy=policy
        )
        return order, policy

    order.status = OrderStatus.CONFIRMED
    order.confirmation_received = True
    order.confirmed_at = datetime.now(UTC)
    await db.flush()
    await audit_service.record_order_confirmed(
        db, actor=actor, order=order, policy=policy
    )

    return order, policy


class OrderNotCancellableError(OrderError):
    pass


async def cancel_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    *,
    reason: str,
    actor: ActorContext | None = None,
) -> Order:
    """Cancel an order — only allowed before any money has moved
    (DRAFT/PENDING_CONFIRMATION/CONFIRMED). Once a payment attempt
    exists, cancellation isn't safe to do generically here — that goes
    through the returns/refunds flow instead, since money may already
    be captured.
    """
    order = await get_order(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=404, detail=f"No order found with id {order_id}"
        )

    cancellable_states = (
        OrderStatus.DRAFT,
        OrderStatus.PENDING_CONFIRMATION,
        OrderStatus.CONFIRMED,
    )
    if order.status not in cancellable_states:
        raise OrderNotCancellableError(
            f"Order {order.public_order_id} is {order.status} and can't be cancelled directly — "
            "use the returns/refunds flow once a payment has been made."
        )

    order.status = OrderStatus.CANCELLED
    await db.flush()

    await audit_service.record_event(
        db,
        actor=actor,
        event_type="order",
        action="cancel_order",
        explanation=f"Order {order.public_order_id} cancelled: {reason}",
        success=True,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        resource_type="order",
        resource_id=order.id,
        amount=order.total,
        currency=order.currency,
    )

    return order
