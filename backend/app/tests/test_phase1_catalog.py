"""Phase 1 tests — catalog service + API routes.

These build their own minimal fixtures per test (rather than depending on
`app/seed.py` having been run) so the suite is self-contained and doesn't
care whether the dev database has been seeded.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Merchant, Product, ProductRelationship
from app.services import catalog_service


async def _make_merchant(db: AsyncSession, **overrides) -> Merchant:
    defaults = {
        "business_name": "Test Electronics",
        "owner_name": "Test Owner",
        "phone": "9000000000",
        "city": "Delhi",
        "state": "Delhi",
    }
    defaults.update(overrides)
    merchant = Merchant(**defaults)
    db.add(merchant)
    await db.flush()
    return merchant


async def _make_product(db: AsyncSession, merchant: Merchant, **overrides) -> Product:
    defaults = {
        "merchant_id": merchant.id,
        "sku": "SKU-1",
        "name": "Wireless Mouse",
        "description": "A mouse",
        "category": "electronics",
        "price": Decimal("799"),
        "currency": "INR",
        "stock_quantity": 10,
        "active": True,
        "metadata_": {},
    }
    defaults.update(overrides)
    product = Product(**defaults)
    db.add(product)
    await db.flush()
    return product


# --- parse_natural_language_query (pure function, no DB) -------------------


def test_parses_max_price_and_stock_filter() -> None:
    filters = catalog_service.parse_natural_language_query(
        "wireless mouse under ₹1000 that is in stock"
    )
    assert filters.max_price == 1000.0
    assert filters.in_stock_only is True
    assert "wireless" in filters.keywords
    assert "mouse" in filters.keywords


def test_parses_hinglish_price_phrase() -> None:
    filters = catalog_service.parse_natural_language_query(
        "mujhe ek wireless mouse chahiye ₹1000 ke andar"
    )
    assert filters.max_price == 1000.0
    assert "wireless" in filters.keywords
    assert "mouse" in filters.keywords
    # filler words should not leak into keywords
    assert "chahiye" not in filters.keywords
    assert "ke" not in filters.keywords


def test_parses_min_price() -> None:
    filters = catalog_service.parse_natural_language_query("keyboard above ₹500")
    assert filters.min_price == 500.0


# --- catalog_service against a real DB --------------------------------


@pytest.mark.asyncio
async def test_get_agent_catalog_filters_by_price_and_stock(
    db_session: AsyncSession,
) -> None:
    merchant = await _make_merchant(db_session)
    await _make_product(
        db_session,
        merchant,
        sku="A",
        name="Cheap in stock",
        price=Decimal("500"),
        stock_quantity=5,
    )
    await _make_product(
        db_session,
        merchant,
        sku="B",
        name="Expensive in stock",
        price=Decimal("2000"),
        stock_quantity=5,
    )
    await _make_product(
        db_session,
        merchant,
        sku="C",
        name="Cheap out of stock",
        price=Decimal("500"),
        stock_quantity=0,
    )
    await db_session.commit()

    results = await catalog_service.get_agent_catalog(
        db_session, max_price=1000, in_stock_only=True
    )
    names = {p.name for p in results}
    assert names == {"Cheap in stock"}


@pytest.mark.asyncio
async def test_get_agent_catalog_includes_out_of_stock_when_not_filtered(
    db_session: AsyncSession,
) -> None:
    merchant = await _make_merchant(db_session)
    await _make_product(
        db_session,
        merchant,
        sku="C",
        name="Out of stock item",
        price=Decimal("699"),
        stock_quantity=0,
    )
    await db_session.commit()

    results = await catalog_service.get_agent_catalog(db_session, max_price=1000)
    assert len(results) == 1
    assert results[0].available is False
    assert results[0].stock == 0


@pytest.mark.asyncio
async def test_never_fabricates_price_reflects_live_db_value(
    db_session: AsyncSession,
) -> None:
    """If the price changes in the DB, the next catalog read must reflect
    it immediately — nothing here should be cached or stale."""
    merchant = await _make_merchant(db_session)
    product = await _make_product(db_session, merchant, price=Decimal("799"))
    await db_session.commit()

    first = await catalog_service.to_agent_product(db_session, product)
    assert first.price == 799.0

    product.price = Decimal("899")
    await db_session.commit()
    await db_session.refresh(product)

    second = await catalog_service.to_agent_product(db_session, product)
    assert second.price == 899.0


@pytest.mark.asyncio
async def test_cross_sell_and_upsell_relationships_are_included(
    db_session: AsyncSession,
) -> None:
    merchant = await _make_merchant(db_session)
    mouse = await _make_product(
        db_session, merchant, sku="MOUSE", name="Wireless Mouse", price=Decimal("799")
    )
    pad = await _make_product(
        db_session, merchant, sku="PAD", name="Mouse Pad", price=Decimal("199")
    )
    pro = await _make_product(
        db_session, merchant, sku="PRO", name="Pro Mouse", price=Decimal("1299")
    )
    db_session.add(
        ProductRelationship(
            merchant_id=merchant.id,
            product_id=mouse.id,
            related_product_id=pad.id,
            relationship_type="CROSS_SELL",
            priority=10,
        )
    )
    db_session.add(
        ProductRelationship(
            merchant_id=merchant.id,
            product_id=mouse.id,
            related_product_id=pro.id,
            relationship_type="UPSELL",
            priority=5,
        )
    )
    await db_session.commit()

    agent_product = await catalog_service.to_agent_product(db_session, mouse)
    assert [r.name for r in agent_product.cross_sell_products] == ["Mouse Pad"]
    assert [r.name for r in agent_product.upsell_products] == ["Pro Mouse"]


@pytest.mark.asyncio
async def test_natural_language_search_end_to_end_scenario(
    db_session: AsyncSession,
) -> None:
    """Reproduces the spec's canonical AI Buyer demo: three merchants sell
    a wireless mouse; only the in-stock, under-budget ones should surface."""
    merchant_a = await _make_merchant(db_session, business_name="Merchant A")
    merchant_b = await _make_merchant(
        db_session, business_name="Merchant B", phone="9000000001"
    )
    merchant_c = await _make_merchant(
        db_session, business_name="Merchant C", phone="9000000002"
    )

    await _make_product(
        db_session,
        merchant_a,
        sku="A1",
        name="Wireless Mouse",
        price=Decimal("799"),
        stock_quantity=23,
    )
    await _make_product(
        db_session,
        merchant_b,
        sku="B1",
        name="Wireless Mouse",
        price=Decimal("899"),
        stock_quantity=7,
    )
    await _make_product(
        db_session,
        merchant_c,
        sku="C1",
        name="Wireless Mouse",
        price=Decimal("699"),
        stock_quantity=0,
    )
    await db_session.commit()

    filters, products = await catalog_service.natural_language_search(
        db_session, "wireless mouse under ₹1000 that is in stock"
    )
    assert filters.max_price == 1000.0
    assert filters.in_stock_only is True
    merchant_names = [p.merchant.business_name for p in products]
    assert merchant_names == [
        "Merchant A",
        "Merchant B",
    ]  # cheapest available first, C excluded (out of stock)


# --- API routes ----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_merchants_route(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_merchant(db_session, business_name="Route Test Store")
    await db_session.commit()

    resp = await client.get("/api/v1/merchants")
    assert resp.status_code == 200
    names = [m["business_name"] for m in resp.json()]
    assert "Route Test Store" in names


@pytest.mark.asyncio
async def test_get_merchant_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/merchants/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_catalog_route_shape(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _make_merchant(db_session)
    await _make_product(db_session, merchant)
    await db_session.commit()

    resp = await client.get("/api/v1/agent/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    product = body["products"][0]
    expected_fields = {
        "id",
        "sku",
        "name",
        "price",
        "currency",
        "stock",
        "available",
        "merchant",
    }
    assert expected_fields.issubset(product)


@pytest.mark.asyncio
async def test_agent_search_route(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _make_merchant(db_session)
    await _make_product(db_session, merchant, price=Decimal("799"), stock_quantity=23)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/agent/search", json={"query": "wireless mouse under 1000"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["interpreted_as"]["max_price"] == 1000.0
