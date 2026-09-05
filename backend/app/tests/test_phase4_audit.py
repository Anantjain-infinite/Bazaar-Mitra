"""Phase 4 tests — audit trail wiring across order/payment/webhook flows."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Buyer,
    Merchant,
    Order,
    Payment,
    Product,
    TransactionPolicy,
)
from app.db.models.enums import ActorType, OrderStatus, PaymentStatus
from app.integrations import razorpay as razorpay_integration
from app.schemas.context import ActorContext
from app.services import audit_service, cart_service, order_service, payment_service

settings = get_settings()


@pytest.fixture(autouse=True)
def _configure_razorpay_test_secrets(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_secret", "test_key_secret")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "test_webhook_secret")
    razorpay_integration.get_client.cache_clear()
    yield
    razorpay_integration.get_client.cache_clear()


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


async def _policy(
    db: AsyncSession, merchant: Merchant, **overrides
) -> TransactionPolicy:
    defaults = {
        "merchant_id": merchant.id,
        "buyer_id": None,
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


async def _order_via_cart(
    db: AsyncSession, merchant: Merchant, buyer: Buyer, price: Decimal
) -> Order:
    product = Product(
        merchant_id=merchant.id,
        sku=f"SKU-{uuid.uuid4().hex[:6]}",
        name="Test Product",
        category="test",
        price=price,
        stock_quantity=10,
        active=True,
    )
    db.add(product)
    await db.flush()
    cart = await cart_service.create_cart(db, merchant.id, buyer.id)
    await cart_service.add_item(db, cart, product.id, 1)
    await db.commit()
    order = await order_service.create_order_from_cart(db, cart.id)
    await db.commit()
    return order


@pytest.mark.asyncio
async def test_order_confirmation_writes_audit_event(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    await db_session.commit()
    order = await _order_via_cart(db_session, merchant, buyer, Decimal("998"))

    actor = ActorContext(
        actor_type=ActorType.MAIN_AGENT,
        agent_name="main_agent",
        session_id=uuid.uuid4(),
    )
    await order_service.confirm_order(db_session, order.id, actor=actor)
    await db_session.commit()

    events = await audit_service.get_events_for_resource(db_session, order.id)
    actions = [e.action for e in events]
    assert "validate_transaction_policy" in actions
    assert "confirm_order" in actions
    confirm_event = next(e for e in events if e.action == "confirm_order")
    assert confirm_event.success is True
    assert confirm_event.confirmation_state == "EXPLICIT"
    assert confirm_event.agent_name == "main_agent"
    assert confirm_event.amount == 998.0


@pytest.mark.asyncio
async def test_policy_blocked_confirmation_still_writes_audit_event(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant, max_transaction_amount=Decimal("500"))
    await db_session.commit()
    order = await _order_via_cart(db_session, merchant, buyer, Decimal("998"))

    await order_service.confirm_order(db_session, order.id)
    await db_session.commit()

    events = await audit_service.get_events_for_resource(db_session, order.id)
    confirm_event = next(e for e in events if e.action == "confirm_order")
    assert confirm_event.success is False
    assert confirm_event.policy_result == "FAIL"
    assert "exceeds" in confirm_event.failure_reason


@pytest.mark.asyncio
async def test_out_of_stock_block_audit_survives_route_rollback(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The route rolls back its session on OutOfStockError — the audit
    event explaining the block must survive that rollback anyway."""
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    product = Product(
        merchant_id=merchant.id,
        sku="LOWSTOCK",
        name="Low Stock Item",
        category="test",
        price=Decimal("100"),
        stock_quantity=1,
        active=True,
    )
    db_session.add(product)
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

    events = await audit_service.list_events(
        db_session, resource_id=None, merchant_id=merchant.id
    )
    order_blocked = [
        e for e in events if e.action == "create_order" and e.success is False
    ]
    assert len(order_blocked) == 1
    assert "out of stock" in order_blocked[0].explanation


@pytest.mark.asyncio
async def test_payment_creation_writes_audit_event(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    await db_session.commit()
    order = await _order_via_cart(db_session, merchant, buyer, Decimal("998"))
    await order_service.confirm_order(db_session, order.id)
    await db_session.commit()

    with patch.object(
        razorpay_integration,
        "create_order",
        return_value={
            "id": "order_fake123",
            "amount": 99800,
            "currency": "INR",
            "status": "created",
        },
    ):
        payment = await payment_service.create_payment_attempt(db_session, order.id)
        await db_session.commit()

    events = await audit_service.get_events_for_resource(db_session, payment.id)
    actions = [e.action for e in events]
    assert "create_payment" in actions
    create_event = next(e for e in events if e.action == "create_payment")
    assert create_event.success is True
    assert create_event.confirmation_state == "EXPLICIT"
    assert create_event.metadata_["razorpay_order_id"] == "order_fake123"
    # never leak anything secret-shaped
    assert "key_secret" not in json.dumps(create_event.metadata_).lower()


@pytest.mark.asyncio
async def test_policy_rejected_payment_audit_survives_rollback(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant, max_transaction_amount=Decimal("500"))
    await db_session.commit()

    product = Product(
        merchant_id=merchant.id,
        sku="EXPENSIVE",
        name="Expensive Item",
        category="test",
        price=Decimal("998"),
        stock_quantity=10,
        active=True,
    )
    db_session.add(product)
    await db_session.flush()
    cart = await cart_service.create_cart(db_session, merchant.id, buyer.id)
    await cart_service.add_item(db_session, cart, product.id, 1)
    await db_session.commit()
    order = await order_service.create_order_from_cart(db_session, cart.id)
    await db_session.commit()
    # Force order into CONFIRMED directly (bypassing policy, to isolate the payment-time check)
    order.status = OrderStatus.CONFIRMED
    await db_session.commit()

    from app.services.payment_service import PolicyRejectedError

    with pytest.raises(PolicyRejectedError):
        await payment_service.create_payment_attempt(db_session, order.id)
    # create_payment_attempt commits internally before raising — nothing to commit here.

    events = await audit_service.get_events_for_resource(db_session, order.id)
    policy_events = [e for e in events if e.action == "validate_transaction_policy"]
    assert len(policy_events) == 1
    assert policy_events[0].success is False


@pytest.mark.asyncio
async def test_webhook_processing_writes_audit_event(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _order_via_cart(db_session, merchant, buyer, Decimal("500"))
    order.status = OrderStatus.PAYMENT_PENDING
    razorpay_order_id = f"order_{uuid.uuid4().hex[:10]}"
    razorpay_payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    payment = Payment(
        order_id=order.id,
        attempt_number=1,
        razorpay_order_id=razorpay_order_id,
        amount=Decimal("500"),
        currency="INR",
        status=PaymentStatus.AUTHORIZED,
        raw_response_metadata={},
    )
    db_session.add(payment)
    await db_session.commit()

    import hashlib
    import hmac

    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {"id": razorpay_payment_id, "order_id": razorpay_order_id}
                }
            },
        }
    ).encode()
    sig = hmac.new(b"test_webhook_secret", body, hashlib.sha256).hexdigest()

    await payment_service.handle_webhook_event(db_session, raw_body=body, signature=sig)
    await db_session.commit()

    events = await audit_service.get_events_for_resource(db_session, payment.id)
    webhook_events = [e for e in events if e.action.startswith("webhook:")]
    assert len(webhook_events) == 1
    assert webhook_events[0].actor_type == ActorType.WEBHOOK.value
    assert webhook_events[0].agent_name == "razorpay_webhook"
    assert webhook_events[0].success is True


@pytest.mark.asyncio
async def test_audit_list_route_filters(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    await db_session.commit()
    order = await _order_via_cart(db_session, merchant, buyer, Decimal("998"))
    await order_service.confirm_order(db_session, order.id)
    await db_session.commit()

    resp = await client.get(f"/api/v1/audit/{order.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert all(e["resource_id"] == str(order.id) for e in body)

    resp = await client.get("/api/v1/audit", params={"merchant_id": str(merchant.id)})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = await client.get(
        "/api/v1/audit", params={"merchant_id": str(merchant.id), "success": "false"}
    )
    assert resp.status_code == 200
    assert all(e["success"] is False for e in resp.json())
