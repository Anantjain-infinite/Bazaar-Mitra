from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Merchant


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


@pytest.mark.asyncio
async def test_identify_buyer_creates_and_reuses(client: AsyncClient) -> None:
    phone = f"9{uuid.uuid4().int % 10**9:09d}"
    resp1 = await client.post(
        "/api/v1/buyers/identify", json={"phone": phone, "name": "Test Buyer"}
    )
    assert resp1.status_code == 200
    buyer_id_1 = resp1.json()["id"]

    resp2 = await client.post("/api/v1/buyers/identify", json={"phone": phone})
    assert resp2.status_code == 200
    assert resp2.json()["id"] == buyer_id_1


@pytest.mark.asyncio
async def test_get_buyer_404(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/buyers/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_orders_filters_by_merchant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant_a = await _merchant(db_session)
    merchant_b = await _merchant(db_session)
    await db_session.commit()

    from app.db.models import Buyer, Cart, Order
    from app.db.models.enums import CartStatus, OrderStatus

    buyer = Buyer(name="Test", phone=f"9{uuid.uuid4().int % 10**9:09d}")
    db_session.add(buyer)
    await db_session.flush()
    cart = Cart(
        merchant_id=merchant_a.id,
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
        public_order_id=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        merchant_id=merchant_a.id,
        buyer_id=buyer.id,
        cart_id=cart.id,
        status=OrderStatus.PAID,
        payment_status="CAPTURED",
        currency="INR",
        subtotal=Decimal("100"),
        discount=Decimal("0"),
        shipping_amount=Decimal("0"),
        total=Decimal("100"),
        confirmation_required=True,
        confirmation_received=True,
    )
    db_session.add(order)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/orders", params={"merchant_id": str(merchant_a.id)}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(
        "/api/v1/orders", params={"merchant_id": str(merchant_b.id)}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 0
