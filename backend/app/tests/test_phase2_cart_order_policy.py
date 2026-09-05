"""Phase 2 tests — policy engine, cart, and order/confirmation flow.

Several of these reproduce the spec's canonical demo scenario as literal
assertions (₹998 total against a ₹1,000 limit passes; ₹1,299 against the
same limit is refused at both order-creation and confirm time).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Buyer,
    Merchant,
    Order,
    Payment,
    Product,
    TransactionPolicy,
)
from app.db.models.enums import OrderStatus, PaymentStatus
from app.services import cart_service, order_service, policy_service
from app.services.order_service import (
    EmptyCartError,
    OutOfStockError,
    PriceOrStockChangedError,
)

# --- fixtures/helpers -------------------------------------------------


async def _merchant(db: AsyncSession, **overrides) -> Merchant:
    defaults = {
        "business_name": f"M-{uuid.uuid4().hex[:8]}",
        "owner_name": "Owner",
        "phone": "9000000000",
        "city": "Delhi",
        "state": "Delhi",
    }
    defaults.update(overrides)
    m = Merchant(**defaults)
    db.add(m)
    await db.flush()
    return m


async def _buyer(db: AsyncSession, **overrides) -> Buyer:
    defaults = {
        "name": f"Buyer-{uuid.uuid4().hex[:8]}",
        "phone": f"9{uuid.uuid4().int % 10**9:09d}",
    }
    defaults.update(overrides)
    b = Buyer(**defaults)
    db.add(b)
    await db.flush()
    return b


async def _product(db: AsyncSession, merchant: Merchant, **overrides) -> Product:
    defaults = {
        "merchant_id": merchant.id,
        "sku": f"SKU-{uuid.uuid4().hex[:6]}",
        "name": "Wireless Mouse",
        "category": "electronics",
        "price": Decimal("799"),
        "currency": "INR",
        "stock_quantity": 10,
        "active": True,
        "metadata_": {},
    }
    defaults.update(overrides)
    p = Product(**defaults)
    db.add(p)
    await db.flush()
    return p


async def _policy(
    db: AsyncSession, merchant: Merchant, buyer: Buyer | None = None, **overrides
) -> TransactionPolicy:
    defaults = {
        "merchant_id": merchant.id,
        "buyer_id": buyer.id if buyer else None,
        "max_transaction_amount": Decimal("1000"),
        "max_daily_amount": Decimal("3000"),
        "currency": "INR",
        "confirmation_required": True,
        "enabled": True,
    }
    defaults.update(overrides)
    pol = TransactionPolicy(**defaults)
    db.add(pol)
    await db.flush()
    return pol


# --- policy_service ------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_passes_under_limit(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    await db_session.commit()

    result = await policy_service.validate_transaction(
        db_session, merchant_id=merchant.id, buyer_id=buyer.id, amount=Decimal("998")
    )
    assert result.allowed is True
    assert result.reasons == []


@pytest.mark.asyncio
async def test_policy_rejects_over_transaction_limit(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    await db_session.commit()

    result = await policy_service.validate_transaction(
        db_session, merchant_id=merchant.id, buyer_id=buyer.id, amount=Decimal("1299")
    )
    assert result.allowed is False
    assert "exceeds the per-transaction limit" in result.reasons[0]


@pytest.mark.asyncio
async def test_policy_rejects_over_daily_limit_from_prior_captured_payments(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(
        db_session,
        merchant,
        max_transaction_amount=Decimal("5000"),
        max_daily_amount=Decimal("1000"),
    )
    product = await _product(db_session, merchant, price=Decimal("900"))
    await db_session.commit()

    # Simulate an already-captured payment earlier today.
    order = Order(
        public_order_id="ORD-TESTDAILY",
        merchant_id=merchant.id,
        buyer_id=buyer.id,
        status=OrderStatus.PAID,
        payment_status=PaymentStatus.CAPTURED,
        currency="INR",
        subtotal=Decimal("900"),
        discount=Decimal("0"),
        shipping_amount=Decimal("0"),
        total=Decimal("900"),
        confirmation_required=True,
        confirmation_received=True,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        Payment(
            order_id=order.id,
            attempt_number=1,
            amount=Decimal("900"),
            currency="INR",
            status=PaymentStatus.CAPTURED,
            raw_response_metadata={},
        )
    )
    await db_session.commit()
    _ = product  # not used directly, just ensures merchant has a catalog

    result = await policy_service.validate_transaction(
        db_session, merchant_id=merchant.id, buyer_id=buyer.id, amount=Decimal("200")
    )
    assert result.allowed is False
    assert result.daily_spend_before == 900.0
    assert "exceeding the daily limit" in result.reasons[0]


@pytest.mark.asyncio
async def test_buyer_level_cap_is_more_restrictive_than_merchant_policy(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session, max_transaction_amount=Decimal("500"))
    await _policy(db_session, merchant, max_transaction_amount=Decimal("5000"))
    await db_session.commit()

    result = await policy_service.validate_transaction(
        db_session, merchant_id=merchant.id, buyer_id=buyer.id, amount=Decimal("800")
    )
    assert result.allowed is False
    assert result.max_transaction_amount == 500.0  # the tighter of the two caps won


# --- cart_service ----------------------------------------------------------


@pytest.mark.asyncio
async def test_add_item_quotes_current_price(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant, price=Decimal("799"))
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    item = await cart_service.add_item(db_session, cart, product.id, 2)
    await db_session.commit()

    assert item.unit_price == Decimal("799")
    assert item.line_total == Decimal("1598")
    assert cart.total == Decimal("1598")


@pytest.mark.asyncio
async def test_cart_item_price_survives_later_product_price_change(
    db_session: AsyncSession,
) -> None:
    """This is the whole point of quoting at add-time: a later catalog
    price change must NOT silently alter what's already in the cart."""
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant, price=Decimal("799"))
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, product.id, 1)
    await db_session.commit()

    product.price = Decimal("999")
    await db_session.commit()

    cart = await cart_service.get_cart(db_session, cart.id)
    assert cart.items[0].unit_price == Decimal("799")  # unchanged


# --- order_service: the safety-critical paths -----------------------------


@pytest.mark.asyncio
async def test_create_order_from_cart_matches_spec_demo_scenario(
    db_session: AsyncSession,
) -> None:
    """₹799 mouse + ₹199 mouse pad = ₹998, against a ₹1,000 limit -> allowed."""
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(
        db_session,
        merchant,
        max_transaction_amount=Decimal("1000"),
        max_daily_amount=Decimal("3000"),
    )
    mouse = await _product(
        db_session, merchant, name="Wireless Mouse", price=Decimal("799")
    )
    pad = await _product(db_session, merchant, name="Mouse Pad", price=Decimal("199"))
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, mouse.id, 1)
    await cart_service.add_item(db_session, cart, pad.id, 1)
    await db_session.commit()

    order = await order_service.create_order_from_cart(db_session, cart.id)
    await db_session.commit()

    assert order.total == Decimal("998")
    assert order.status == OrderStatus.PENDING_CONFIRMATION

    policy = await policy_service.validate_transaction(
        db_session, merchant_id=merchant.id, buyer_id=buyer.id, amount=order.total
    )
    assert policy.allowed is True

    confirmed_order, confirm_policy = await order_service.confirm_order(
        db_session, order.id
    )
    await db_session.commit()
    assert confirmed_order.status == OrderStatus.CONFIRMED
    assert confirmed_order.confirmation_received is True
    assert confirm_policy.allowed is True


@pytest.mark.asyncio
async def test_confirm_refuses_when_policy_fails_and_leaves_order_pending(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(
        db_session,
        merchant,
        max_transaction_amount=Decimal("1000"),
        max_daily_amount=Decimal("3000"),
    )
    pro_mouse = await _product(
        db_session, merchant, name="Pro Mouse", price=Decimal("1299")
    )
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, pro_mouse.id, 1)
    await db_session.commit()

    order = await order_service.create_order_from_cart(db_session, cart.id)
    await db_session.commit()

    confirmed_order, policy = await order_service.confirm_order(db_session, order.id)
    await db_session.commit()

    assert policy.allowed is False
    assert (
        confirmed_order.status == OrderStatus.PENDING_CONFIRMATION
    )  # never silently confirmed
    assert confirmed_order.confirmation_received is False


@pytest.mark.asyncio
async def test_price_drift_blocks_order_creation_unless_acknowledged(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant, price=Decimal("210"))
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, product.id, 2)
    await db_session.commit()

    product.price = Decimal("260")  # merchant changes price after it was quoted
    await db_session.commit()

    with pytest.raises(PriceOrStockChangedError) as exc_info:
        await order_service.create_order_from_cart(db_session, cart.id)
    assert exc_info.value.issues[0].quoted_price == 210.0
    assert exc_info.value.issues[0].current_price == 260.0

    # Cart must NOT have been converted by the failed attempt.
    cart = await cart_service.get_cart(db_session, cart.id)
    assert cart.status.value == "OPEN"

    # Acknowledging re-quotes to the live price and proceeds.
    order = await order_service.create_order_from_cart(
        db_session, cart.id, acknowledge_price_change=True
    )
    await db_session.commit()
    assert order.items[0].quoted_unit_price == Decimal("260")
    assert order.total == Decimal("520")


@pytest.mark.asyncio
async def test_out_of_stock_always_blocks_even_with_acknowledge_flag(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant, stock_quantity=1)
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, product.id, 3)
    await db_session.commit()

    with pytest.raises(OutOfStockError):
        await order_service.create_order_from_cart(
            db_session, cart.id, acknowledge_price_change=True
        )


@pytest.mark.asyncio
async def test_empty_cart_cannot_become_an_order(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await db_session.commit()
    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await db_session.commit()

    with pytest.raises(EmptyCartError):
        await order_service.create_order_from_cart(db_session, cart.id)


@pytest.mark.asyncio
async def test_order_creation_is_idempotent_for_same_cart(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant)
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, product.id, 1)
    await db_session.commit()

    order1 = await order_service.create_order_from_cart(db_session, cart.id)
    await db_session.commit()
    order2 = await order_service.create_order_from_cart(db_session, cart.id)
    await db_session.commit()

    assert order1.id == order2.id
    assert order1.public_order_id == order2.public_order_id


@pytest.mark.asyncio
async def test_cannot_add_items_to_a_converted_cart(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant)
    await db_session.commit()

    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, product.id, 1)
    await db_session.commit()
    await order_service.create_order_from_cart(db_session, cart.id)
    await db_session.commit()

    cart = await cart_service.get_cart(db_session, cart.id)
    from app.services.cart_service import CartNotOpenError

    with pytest.raises(CartNotOpenError):
        await cart_service.add_item(db_session, cart, product.id, 1)


# --- API route-level tests ---------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_via_http_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(
        db_session,
        merchant,
        max_transaction_amount=Decimal("1000"),
        max_daily_amount=Decimal("3000"),
    )
    mouse = await _product(
        db_session, merchant, name="Wireless Mouse", price=Decimal("799")
    )
    pad = await _product(db_session, merchant, name="Mouse Pad", price=Decimal("199"))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/carts",
        json={"merchant_id": str(merchant.id), "buyer_id": str(buyer.id)},
    )
    assert resp.status_code == 201
    cart_id = resp.json()["id"]

    resp = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        json={"product_id": str(mouse.id), "quantity": 1},
    )
    assert resp.status_code == 201
    resp = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        json={"product_id": str(pad.id), "quantity": 1},
    )
    assert resp.status_code == 201

    resp = await client.post("/api/v1/orders", json={"cart_id": cart_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["order"]["total"] == 998.0
    assert body["policy"]["allowed"] is True
    order_id = body["order"]["id"]

    resp = await client.post(f"/api/v1/orders/{order_id}/confirm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order"]["status"] == "CONFIRMED"
    assert body["order"]["confirmation_received"] is True


@pytest.mark.asyncio
async def test_over_limit_order_refused_at_confirm_via_http(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(
        db_session,
        merchant,
        max_transaction_amount=Decimal("1000"),
        max_daily_amount=Decimal("3000"),
    )
    pro_mouse = await _product(
        db_session, merchant, name="Pro Mouse", price=Decimal("1299")
    )
    await db_session.commit()

    resp = await client.post(
        "/api/v1/carts",
        json={"merchant_id": str(merchant.id), "buyer_id": str(buyer.id)},
    )
    cart_id = resp.json()["id"]
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        json={"product_id": str(pro_mouse.id), "quantity": 1},
    )

    resp = await client.post("/api/v1/orders", json={"cart_id": cart_id})
    assert resp.json()["policy"]["allowed"] is False
    order_id = resp.json()["order"]["id"]

    resp = await client.post(f"/api/v1/orders/{order_id}/confirm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order"]["status"] == "PENDING_CONFIRMATION"
    assert body["policy"]["allowed"] is False


@pytest.mark.asyncio
async def test_out_of_stock_returns_409_via_http(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = await _product(db_session, merchant, stock_quantity=1)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/carts",
        json={"merchant_id": str(merchant.id), "buyer_id": str(buyer.id)},
    )
    cart_id = resp.json()["id"]
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        json={"product_id": str(product.id), "quantity": 5},
    )

    resp = await client.post("/api/v1/orders", json={"cart_id": cart_id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "out_of_stock"


@pytest.mark.asyncio
async def test_cart_for_wrong_merchant_product_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant_a = await _merchant(db_session)
    merchant_b = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product_b = await _product(db_session, merchant_b)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/carts",
        json={"merchant_id": str(merchant_a.id), "buyer_id": str(buyer.id)},
    )
    cart_id = resp.json()["id"]

    resp = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        json={"product_id": str(product_b.id), "quantity": 1},
    )
    assert resp.status_code == 400
