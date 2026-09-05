"""Phase 3 tests — payments, signature verification, and webhooks.

Only `razorpay_integration.create_order` is mocked (it's the one call
that needs the real Razorpay network, which this environment can't
reach). Every signature-verification test below uses REAL HMAC
computation against a real (test) secret — there is nothing to mock
there, since verification never talks to Razorpay's servers at all.
"""

from __future__ import annotations

import hashlib
import hmac
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
    Cart,
    Merchant,
    Order,
    OrderItem,
    Payment,
    Product,
    TransactionPolicy,
)
from app.db.models.enums import CartStatus, OrderStatus, PaymentStatus
from app.integrations import razorpay as razorpay_integration
from app.services import payment_service

settings = get_settings()
TEST_KEY_SECRET = "test_key_secret"
TEST_WEBHOOK_SECRET = "test_webhook_secret"


@pytest.fixture(autouse=True)
def _configure_razorpay_test_secrets(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_fake")
    monkeypatch.setattr(settings, "razorpay_key_secret", TEST_KEY_SECRET)
    monkeypatch.setattr(settings, "razorpay_webhook_secret", TEST_WEBHOOK_SECRET)
    razorpay_integration.get_client.cache_clear()
    yield
    razorpay_integration.get_client.cache_clear()


def sign_payment(order_id: str, payment_id: str, secret: str = TEST_KEY_SECRET) -> str:
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_webhook(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def fake_razorpay_order(amount_paise: int, order_id: str | None = None) -> dict:
    return {
        "id": order_id or f"order_fake_{uuid.uuid4().hex[:12]}",
        "amount": amount_paise,
        "currency": "INR",
        "status": "created",
        "receipt": "ORD-TEST",
    }


# --- helpers -------------------------------------------------------------


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
        "max_transaction_amount": Decimal("5000"),
        "max_daily_amount": Decimal("10000"),
        "currency": "INR",
        "confirmation_required": True,
        "enabled": True,
    }
    defaults.update(overrides)
    pol = TransactionPolicy(**defaults)
    db.add(pol)
    await db.flush()
    return pol


async def _confirmed_order(
    db: AsyncSession, merchant: Merchant, buyer: Buyer, total: Decimal
) -> Order:
    """Build an order already in CONFIRMED state, ready for payment —
    skips the cart/order_service flow since that's covered in Phase 2 tests.
    """
    product = Product(
        merchant_id=merchant.id,
        sku=f"SKU-{uuid.uuid4().hex[:6]}",
        name="Test Product",
        category="test",
        price=total,
        stock_quantity=10,
        active=True,
    )
    db.add(product)
    await db.flush()

    cart = Cart(
        merchant_id=merchant.id,
        buyer_id=buyer.id,
        status=CartStatus.CONVERTED,
        currency="INR",
        subtotal=total,
        discount=Decimal("0"),
        total=total,
    )
    db.add(cart)
    await db.flush()

    order = Order(
        public_order_id=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        merchant_id=merchant.id,
        buyer_id=buyer.id,
        cart_id=cart.id,
        status=OrderStatus.CONFIRMED,
        payment_status="NONE",
        currency="INR",
        subtotal=total,
        discount=Decimal("0"),
        shipping_amount=Decimal("0"),
        total=total,
        confirmation_required=True,
        confirmation_received=True,
    )
    db.add(order)
    await db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            sku=product.sku,
            name_snapshot=product.name,
            quantity=1,
            quoted_unit_price=total,
            final_unit_price=total,
            total=total,
        )
    )
    await db.flush()
    return order


# --- integration plumbing (real crypto, no network) -----------------------


def test_verify_payment_signature_matches_documented_formula() -> None:
    order_id, payment_id = "order_ABC123", "pay_XYZ789"
    valid_sig = sign_payment(order_id, payment_id)
    with patch.object(
        razorpay_integration.settings, "razorpay_key_secret", TEST_KEY_SECRET
    ):
        assert (
            razorpay_integration.verify_payment_signature(
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                razorpay_signature=valid_sig,
            )
            is True
        )
        assert (
            razorpay_integration.verify_payment_signature(
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                razorpay_signature="tampered",
            )
            is False
        )


def test_verify_webhook_signature_uses_raw_body() -> None:
    body = b'{"event": "payment.captured", "payload": {}}'
    valid_sig = sign_webhook(body)
    with patch.object(
        razorpay_integration.settings, "razorpay_webhook_secret", TEST_WEBHOOK_SECRET
    ):
        assert (
            razorpay_integration.verify_webhook_signature(
                raw_body=body, signature=valid_sig
            )
            is True
        )
        # A re-serialized (even semantically identical) body must NOT verify —
        # this is the documented "sign the raw bytes" pitfall.
        reserialized = json.dumps(json.loads(body)).encode()
        if reserialized != body:
            assert (
                razorpay_integration.verify_webhook_signature(
                    raw_body=reserialized, signature=valid_sig
                )
                is False
            )


def test_rupees_to_paise_conversion() -> None:
    assert razorpay_integration.rupees_to_paise(Decimal("998")) == 99800
    assert razorpay_integration.rupees_to_paise(Decimal("222.25")) == 22225
    assert (
        razorpay_integration.rupees_to_paise(Decimal("199.999")) == 20000
    )  # rounds half up


# --- payment_service: create_payment_attempt (create_order mocked) --------


@pytest.mark.asyncio
async def test_create_payment_attempt_requires_confirmed_order(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    product = Product(
        merchant_id=merchant.id,
        sku="X",
        name="X",
        category="t",
        price=Decimal("100"),
        stock_quantity=5,
        active=True,
    )
    db_session.add(product)
    await db_session.flush()
    cart = Cart(
        merchant_id=merchant.id,
        buyer_id=buyer.id,
        status=CartStatus.CONVERTED,
        currency="INR",
        subtotal=Decimal("100"),
        discount=Decimal("0"),
        total=Decimal("100"),
    )
    db_session.add(cart)
    await db_session.flush()
    order = Order(
        public_order_id="ORD-DRAFTTEST",
        merchant_id=merchant.id,
        buyer_id=buyer.id,
        cart_id=cart.id,
        status=OrderStatus.PENDING_CONFIRMATION,
        payment_status="NONE",
        currency="INR",
        subtotal=Decimal("100"),
        discount=Decimal("0"),
        shipping_amount=Decimal("0"),
        total=Decimal("100"),
        confirmation_required=True,
        confirmation_received=False,
    )
    db_session.add(order)
    await db_session.commit()

    from app.services.payment_service import OrderNotPayableError

    with pytest.raises(OrderNotPayableError):
        await payment_service.create_payment_attempt(db_session, order.id)


@pytest.mark.asyncio
async def test_create_payment_attempt_success(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant, max_transaction_amount=Decimal("1000"))
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("998"))
    await db_session.commit()

    with patch.object(
        razorpay_integration, "create_order", return_value=fake_razorpay_order(99800)
    ) as mock_create:
        payment = await payment_service.create_payment_attempt(db_session, order.id)
        await db_session.commit()

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["amount_rupees"] == Decimal("998")
    assert call_kwargs["receipt"] == order.public_order_id

    assert payment.attempt_number == 1
    assert payment.status == PaymentStatus.CREATED
    assert payment.razorpay_order_id is not None

    await db_session.refresh(order)
    assert order.status == OrderStatus.PAYMENT_PENDING


@pytest.mark.asyncio
async def test_create_payment_attempt_blocked_by_policy(
    db_session: AsyncSession,
) -> None:
    """Defense in depth: even though the order was CONFIRMED earlier,
    payment creation re-checks policy right before money moves."""
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant, max_transaction_amount=Decimal("500"))
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("998"))
    await db_session.commit()

    from app.services.payment_service import PolicyRejectedError

    with patch.object(razorpay_integration, "create_order") as mock_create:
        with pytest.raises(PolicyRejectedError):
            await payment_service.create_payment_attempt(db_session, order.id)
        mock_create.assert_not_called()  # never even reaches Razorpay


@pytest.mark.asyncio
async def test_create_payment_attempt_gateway_failure_writes_no_payment_row(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
    await db_session.commit()

    from app.services.payment_service import PaymentGatewayUnavailableError

    with (
        patch.object(
            razorpay_integration,
            "create_order",
            side_effect=ConnectionError("network down"),
        ),
        pytest.raises(PaymentGatewayUnavailableError),
    ):
        await payment_service.create_payment_attempt(db_session, order.id)

    await db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED  # unchanged — nothing was attempted


@pytest.mark.asyncio
async def test_idempotency_key_prevents_duplicate_razorpay_orders(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
    await db_session.commit()

    key = f"idem-{uuid.uuid4().hex}"
    with patch.object(
        razorpay_integration, "create_order", return_value=fake_razorpay_order(50000)
    ) as mock_create:
        p1 = await payment_service.create_payment_attempt(
            db_session, order.id, idempotency_key=key
        )
        await db_session.commit()
        p2 = await payment_service.create_payment_attempt(
            db_session, order.id, idempotency_key=key
        )
        await db_session.commit()

    assert p1.id == p2.id
    mock_create.assert_called_once()  # second call short-circuited, never hit "Razorpay"


@pytest.mark.asyncio
async def test_retry_creates_new_attempt_after_failure(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
    await db_session.commit()

    with patch.object(
        razorpay_integration, "create_order", return_value=fake_razorpay_order(50000)
    ):
        payment1 = await payment_service.create_payment_attempt(db_session, order.id)
        await db_session.commit()

    # Simulate the first attempt failing (e.g. via webhook/verify).
    payment1.status = PaymentStatus.FAILED
    order.status = OrderStatus.PAYMENT_FAILED
    await db_session.commit()

    with patch.object(
        razorpay_integration, "create_order", return_value=fake_razorpay_order(50000)
    ):
        payment2 = await payment_service.create_payment_attempt(db_session, order.id)
        await db_session.commit()

    assert payment2.attempt_number == 2
    assert payment2.id != payment1.id
    await db_session.refresh(order)
    assert order.status == OrderStatus.PAYMENT_PENDING


# --- verify_payment (real crypto) -----------------------------------------


@pytest.mark.asyncio
async def test_verify_payment_valid_signature_authorizes_but_does_not_mark_paid(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
    order.status = OrderStatus.PAYMENT_PENDING
    razorpay_order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment = Payment(
        order_id=order.id,
        attempt_number=1,
        razorpay_order_id=razorpay_order_id,
        amount=Decimal("500"),
        currency="INR",
        status=PaymentStatus.CREATED,
        raw_response_metadata={},
    )
    db_session.add(payment)
    await db_session.commit()

    razorpay_payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    valid_sig = sign_payment(razorpay_order_id, razorpay_payment_id)

    result = await payment_service.verify_payment(
        db_session,
        payment.id,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=valid_sig,
    )
    await db_session.commit()

    assert result.status == PaymentStatus.AUTHORIZED
    await db_session.refresh(order)
    assert order.status == OrderStatus.PAYMENT_PENDING  # NOT paid from signature alone
    assert order.payment_status == "AUTHORIZED"


@pytest.mark.asyncio
async def test_verify_payment_invalid_signature_marks_failed(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
    order.status = OrderStatus.PAYMENT_PENDING
    razorpay_order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment = Payment(
        order_id=order.id,
        attempt_number=1,
        razorpay_order_id=razorpay_order_id,
        amount=Decimal("500"),
        currency="INR",
        status=PaymentStatus.CREATED,
        raw_response_metadata={},
    )
    db_session.add(payment)
    await db_session.commit()

    from app.services.payment_service import InvalidSignatureError

    with pytest.raises(InvalidSignatureError):
        await payment_service.verify_payment(
            db_session,
            payment.id,
            razorpay_payment_id="pay_fake",
            razorpay_signature="0" * 64,
        )
    await db_session.commit()

    await db_session.refresh(payment)
    await db_session.refresh(order)
    assert payment.status == PaymentStatus.FAILED
    assert order.status == OrderStatus.PAYMENT_FAILED


# --- webhook handling (real crypto) ---------------------------------------


@pytest.mark.asyncio
async def test_webhook_payment_captured_marks_order_paid(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
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

    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": razorpay_payment_id,
                        "order_id": razorpay_order_id,
                        "status": "captured",
                    }
                }
            },
        }
    ).encode()
    sig = sign_webhook(body)

    result = await payment_service.handle_webhook_event(
        db_session, raw_body=body, signature=sig
    )
    await db_session.commit()

    assert result["processed"] is True
    await db_session.refresh(order)
    await db_session.refresh(payment)
    assert order.status == OrderStatus.PAID
    assert payment.status == PaymentStatus.CAPTURED


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected(db_session: AsyncSession) -> None:
    from app.services.payment_service import InvalidWebhookSignatureError

    body = json.dumps({"event": "payment.captured", "payload": {}}).encode()
    with pytest.raises(InvalidWebhookSignatureError):
        await payment_service.handle_webhook_event(
            db_session, raw_body=body, signature="tampered"
        )


@pytest.mark.asyncio
async def test_webhook_replay_is_idempotent(db_session: AsyncSession) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
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
    sig = sign_webhook(body)

    await payment_service.handle_webhook_event(db_session, raw_body=body, signature=sig)
    await db_session.commit()
    result2 = await payment_service.handle_webhook_event(
        db_session, raw_body=body, signature=sig
    )
    await db_session.commit()

    assert result2.get("idempotent_noop") is True


@pytest.mark.asyncio
async def test_webhook_payment_failed_marks_order_payment_failed(
    db_session: AsyncSession,
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
    order.status = OrderStatus.PAYMENT_PENDING
    razorpay_order_id = f"order_{uuid.uuid4().hex[:10]}"
    razorpay_payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    payment = Payment(
        order_id=order.id,
        attempt_number=1,
        razorpay_order_id=razorpay_order_id,
        amount=Decimal("500"),
        currency="INR",
        status=PaymentStatus.CREATED,
        raw_response_metadata={},
    )
    db_session.add(payment)
    await db_session.commit()

    body = json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": razorpay_payment_id,
                        "order_id": razorpay_order_id,
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_description": "Card declined",
                    }
                }
            },
        }
    ).encode()
    sig = sign_webhook(body)

    await payment_service.handle_webhook_event(db_session, raw_body=body, signature=sig)
    await db_session.commit()

    await db_session.refresh(order)
    await db_session.refresh(payment)
    assert order.status == OrderStatus.PAYMENT_FAILED
    assert payment.status == PaymentStatus.FAILED
    assert payment.failure_reason == "Card declined"


# --- API routes ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_payment_route(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant, max_transaction_amount=Decimal("1000"))
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("998"))
    await db_session.commit()

    with patch.object(
        razorpay_integration, "create_order", return_value=fake_razorpay_order(99800)
    ):
        resp = await client.post(
            "/api/v1/payments/create", json={"order_id": str(order.id)}
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["amount_paise"] == 99800
    assert "razorpay_key_id" in body
    assert body["payment"]["status"] == "CREATED"
    # Never leak the secret via this or any response.
    assert "key_secret" not in json.dumps(body).lower()


@pytest.mark.asyncio
async def test_webhook_route_end_to_end(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    order = await _confirmed_order(db_session, merchant, buyer, Decimal("500"))
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
    sig = sign_webhook(body)

    resp = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sig}
    )
    assert resp.status_code == 200

    resp = await client.get(f"/api/v1/orders/{order.id}")
    assert resp.json()["status"] == "PAID"
    assert len(resp.json()["payments"]) == 1


@pytest.mark.asyncio
async def test_webhook_route_rejects_bad_signature(client: AsyncClient) -> None:
    body = json.dumps({"event": "payment.captured", "payload": {}}).encode()
    resp = await client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "bad"},
    )
    assert resp.status_code == 400
