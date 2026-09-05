"""Phase 6-8 tests — AI Buyer, recommendation tracking, and the growth agent.

`test_agent_context_survives_across_separate_http_requests` is a
deliberate regression test for a real bug this phase caught: a few tool
functions called `save_context` (a flush) *after* their own `db.commit()`,
so state changes like `order_id`/`payment_id` never actually persisted -
invisible in earlier tests because the old `client` fixture shared one
continuous session across a whole test, masking exactly this class of bug.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import buyer_agent, growth_agent, main_agent
from app.agents.context import load_context
from app.db.models import (
    Buyer,
    Merchant,
    Product,
    ProductRelationship,
    TransactionPolicy,
)
from app.db.models.enums import CampaignStatus, OrderStatus, PaymentStatus
from app.integrations import razorpay as razorpay_integration
from app.services import growth_service, recommendation_service, session_service


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


async def _three_merchant_mouse_scenario(db: AsyncSession):
    """Reproduces the spec's canonical Merchant A/B/C comparison."""
    a = await _merchant(db, business_name="Merchant A")
    b = await _merchant(db, business_name="Merchant B", phone="9000000001")
    c = await _merchant(db, business_name="Merchant C", phone="9000000002")
    await _policy(db, a, max_transaction_amount=Decimal("1000"))
    await _policy(db, b, max_transaction_amount=Decimal("1000"))
    await _policy(db, c, max_transaction_amount=Decimal("1000"))

    mouse_a = Product(
        merchant_id=a.id,
        sku="A1",
        name="Wireless Mouse",
        category="electronics",
        price=Decimal("799"),
        currency="INR",
        stock_quantity=23,
        active=True,
        metadata_={},
    )
    mouse_b = Product(
        merchant_id=b.id,
        sku="B1",
        name="Wireless Mouse",
        category="electronics",
        price=Decimal("899"),
        currency="INR",
        stock_quantity=7,
        active=True,
        metadata_={},
    )
    mouse_c = Product(
        merchant_id=c.id,
        sku="C1",
        name="Wireless Mouse",
        category="electronics",
        price=Decimal("699"),
        currency="INR",
        stock_quantity=0,
        active=True,
        metadata_={},
    )
    pad_a = Product(
        merchant_id=a.id,
        sku="A2",
        name="Mouse Pad",
        category="electronics",
        price=Decimal("199"),
        currency="INR",
        stock_quantity=50,
        active=True,
        metadata_={},
    )
    db.add_all([mouse_a, mouse_b, mouse_c, pad_a])
    await db.flush()
    db.add(
        ProductRelationship(
            merchant_id=a.id,
            product_id=mouse_a.id,
            related_product_id=pad_a.id,
            relationship_type="CROSS_SELL",
            priority=10,
        )
    )
    buyer = await _buyer(db)
    await db.commit()
    return {
        "a": a,
        "b": b,
        "c": c,
        "mouse_a": mouse_a,
        "mouse_b": mouse_b,
        "mouse_c": mouse_c,
        "pad_a": pad_a,
        "buyer": buyer,
    }


# --- AI Buyer (Phase 6) ----------------------------------------------------


@pytest.mark.asyncio
async def test_discover_and_compare_matches_spec_demo(db_session: AsyncSession) -> None:
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(db_session, buyer_id=s["buyer"].id)
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    result = await buyer_agent.discover_and_compare(
        db_session, ctx, "wireless mouse under 1000 that is in stock"
    )

    assert result["ok"] is True
    assert result["selected"]["merchant_name"] == "Merchant A"
    assert result["selected"]["price"] == 799.0
    assert "cheaper" in result["explanation"]


@pytest.mark.asyncio
async def test_buy_best_available_selects_and_checks_out(
    db_session: AsyncSession,
) -> None:
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(db_session, buyer_id=s["buyer"].id)
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    result = await buyer_agent.buy_best_available(
        db_session, ctx, "wireless mouse under 1000 in stock"
    )

    assert result["ok"] is True
    assert result["checkout"]["total"] == 799.0
    assert result["checkout"]["policy"]["allowed"] is True
    assert result["checkout"]["requires_explicit_confirmation"] is True


@pytest.mark.asyncio
async def test_discover_and_compare_no_match(db_session: AsyncSession) -> None:
    buyer = await _buyer(db_session)
    await db_session.commit()
    session = await session_service.create_session(db_session, buyer_id=buyer.id)
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    result = await buyer_agent.discover_and_compare(
        db_session, ctx, "a nonexistent product xyz123"
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_agent_context_survives_across_separate_http_requests(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression test - see module docstring. Each of these client
    calls now genuinely gets its own DB session (matching production),
    so this proves order_id/payment_id are actually persisted, not just
    visible within one shared uncommitted transaction.
    """
    merchant = await _merchant(db_session)
    buyer = await _buyer(db_session)
    await _policy(db_session, merchant, max_transaction_amount=Decimal("1000"))
    product = Product(
        merchant_id=merchant.id,
        sku="REGR",
        name="Wireless Mouse",
        category="electronics",
        price=Decimal("799"),
        currency="INR",
        stock_quantity=10,
        active=True,
        metadata_={},
    )
    db_session.add(product)
    await db_session.commit()

    resp = await client.post("/api/v1/agent/sessions", json={"buyer_id": str(buyer.id)})
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    resp = await client.post(
        f"/api/v1/agent/buy-best-available?session_id={session_id}",
        json={"query": "wireless mouse under 1000"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp = await client.get(f"/api/v1/agent/sessions/{session_id}")
    assert resp.status_code == 200
    session_state = resp.json()
    assert session_state["order_id"] is not None, (
        "order_id did not survive across separate requests"
    )

    resp = await client.post(f"/api/v1/agent/confirm?session_id={session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, (
        f"confirm failed to find the order from a prior request: {body}"
    )
    assert body["status"] == "CONFIRMED"

    with patch.object(
        razorpay_integration,
        "create_order",
        return_value={
            "id": "order_regr_fake",
            "amount": 79900,
            "currency": "INR",
            "status": "created",
        },
    ):
        resp = await client.post(f"/api/v1/agent/pay?session_id={session_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp = await client.get(f"/api/v1/agent/sessions/{session_id}")
    assert resp.json()["payment_id"] is not None, (
        "payment_id did not survive across separate requests"
    )


# --- Recommendation lifecycle (Phase 7) ------------------------------------


@pytest.mark.asyncio
async def test_recommendation_shown_accepted_converted_lifecycle(
    db_session: AsyncSession,
) -> None:
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=s["buyer"].id, merchant_id=s["a"].id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, s["mouse_a"].id, 1)
    rec_result = await main_agent.get_recommendations(db_session, ctx, s["mouse_a"].id)
    assert rec_result["ok"] is True
    pad_ref = rec_result["cross_sell"][0]
    rec_id = uuid.UUID(pad_ref["recommendation_id"])

    metrics_before = await recommendation_service.get_recommendation_metrics(
        db_session, s["a"].id
    )
    assert metrics_before["recommendations_shown"] >= 1
    assert metrics_before["accepted"] == 0

    add_result = await main_agent.add_to_cart(db_session, ctx, s["pad_a"].id, 1)
    assert add_result["accepted_recommendation_id"] == str(rec_id)

    metrics_after_accept = await recommendation_service.get_recommendation_metrics(
        db_session, s["a"].id
    )
    assert metrics_after_accept["accepted"] == 1
    assert metrics_after_accept["converted"] == 0

    checkout_result = await main_agent.checkout(db_session, ctx)
    assert checkout_result["ok"] is True
    assert checkout_result["total"] == 998.0

    await main_agent.request_payment_confirmation_and_confirm(db_session, ctx)

    from app.services import order_service

    order = await order_service.get_order(db_session, ctx.order_id)
    order.status = OrderStatus.PAID
    order.payment_status = PaymentStatus.CAPTURED.value
    await db_session.commit()
    converted = await recommendation_service.mark_converted_for_order(db_session, order)
    await db_session.commit()

    assert len(converted) == 1
    assert converted[0].id == rec_id
    assert float(converted[0].revenue_impact) == 199.0

    metrics_final = await recommendation_service.get_recommendation_metrics(
        db_session, s["a"].id
    )
    assert metrics_final["converted"] == 1
    assert metrics_final["ai_assisted_revenue"] == 199.0


@pytest.mark.asyncio
async def test_conversion_wired_through_real_webhook(db_session: AsyncSession) -> None:
    """Same lifecycle as above, but conversion triggered by the REAL
    webhook handler (payment_service), not called directly - proves the
    Phase 3 <-> Phase 7 wiring actually works end-to-end.
    """
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=s["buyer"].id, merchant_id=s["a"].id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, s["mouse_a"].id, 1)
    await main_agent.get_recommendations(db_session, ctx, s["mouse_a"].id)
    await main_agent.add_to_cart(db_session, ctx, s["pad_a"].id, 1)
    await main_agent.checkout(db_session, ctx)
    await main_agent.request_payment_confirmation_and_confirm(db_session, ctx)

    from app.db.models import Payment
    from app.services import payment_service

    razorpay_order_id = f"order_{uuid.uuid4().hex[:10]}"
    razorpay_payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    payment = Payment(
        order_id=ctx.order_id,
        attempt_number=1,
        razorpay_order_id=razorpay_order_id,
        amount=Decimal("998"),
        currency="INR",
        status=PaymentStatus.AUTHORIZED,
        raw_response_metadata={},
    )
    db_session.add(payment)
    await db_session.commit()

    import hashlib
    import hmac

    from app.config import get_settings

    settings = get_settings()
    with patch.object(settings, "razorpay_webhook_secret", "test_secret"):
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": razorpay_payment_id,
                            "order_id": razorpay_order_id,
                        }
                    }
                },
            }
        ).encode()
        sig = hmac.new(b"test_secret", body, hashlib.sha256).hexdigest()
        await payment_service.handle_webhook_event(
            db_session, raw_body=body, signature=sig
        )
    await db_session.commit()

    metrics = await recommendation_service.get_recommendation_metrics(
        db_session, s["a"].id
    )
    assert metrics["converted"] == 1
    assert metrics["ai_assisted_revenue"] == 199.0


# --- Growth agent (Phase 8) -------------------------------------------


@pytest.mark.asyncio
async def test_revenue_metrics_reflects_real_paid_orders(
    db_session: AsyncSession,
) -> None:
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=s["buyer"].id, merchant_id=s["a"].id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, s["mouse_a"].id, 1)
    await main_agent.checkout(db_session, ctx)
    await main_agent.request_payment_confirmation_and_confirm(db_session, ctx)

    from app.services import order_service

    order = await order_service.get_order(db_session, ctx.order_id)
    order.status = OrderStatus.PAID
    order.payment_status = PaymentStatus.CAPTURED.value
    await db_session.commit()

    metrics = await growth_service.get_revenue_metrics(db_session, s["a"].id)
    assert metrics["revenue"] == 799.0
    assert metrics["orders"] == 1
    assert metrics["average_order_value"] == 799.0


@pytest.mark.asyncio
async def test_growth_agent_requires_merchant_session(db_session: AsyncSession) -> None:
    buyer = await _buyer(db_session)
    await db_session.commit()
    session = await session_service.create_session(db_session, buyer_id=buyer.id)
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    result = await growth_agent.get_revenue_metrics(db_session, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_campaign_lifecycle_requires_approval_before_execution(
    db_session: AsyncSession,
) -> None:
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=s["buyer"].id, merchant_id=s["a"].id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    draft_result = await growth_agent.create_campaign_draft(
        db_session,
        ctx,
        campaign_type="cross_sell_offer",
        offer={"discount_price": 149, "product_id": str(s["pad_a"].id)},
        message="Get a mouse pad for Rs149 with your mouse!",
        audience_definition={"purchased_product_id": str(s["mouse_a"].id)},
    )
    assert draft_result["status"] == "PENDING_APPROVAL"
    campaign_id = uuid.UUID(draft_result["campaign_id"])

    with pytest.raises(ValueError, match="must be APPROVED"):
        await growth_service.execute_campaign(
            db_session, campaign_id, audience_buyer_ids=[s["buyer"].id]
        )

    approve_result = await growth_agent.approve_campaign(
        db_session, ctx, campaign_id, "merchant_owner"
    )
    assert approve_result["ok"] is True
    assert approve_result["status"] == "APPROVED"

    campaign = await growth_service.execute_campaign(
        db_session, campaign_id, audience_buyer_ids=[s["buyer"].id]
    )
    await db_session.commit()
    assert campaign.status == CampaignStatus.RUNNING

    metrics = await growth_service.get_campaign_metrics(db_session, campaign_id)
    assert metrics["targeted"] == 1
    assert metrics["sent"] == 1


@pytest.mark.asyncio
async def test_cross_sell_opportunities_ranked_by_revenue(
    db_session: AsyncSession,
) -> None:
    s = await _three_merchant_mouse_scenario(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=s["buyer"].id, merchant_id=s["a"].id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, s["mouse_a"].id, 1)
    await main_agent.get_recommendations(db_session, ctx, s["mouse_a"].id)
    await main_agent.add_to_cart(db_session, ctx, s["pad_a"].id, 1)
    await main_agent.checkout(db_session, ctx)
    await main_agent.request_payment_confirmation_and_confirm(db_session, ctx)

    from app.services import order_service

    order = await order_service.get_order(db_session, ctx.order_id)
    order.status = OrderStatus.PAID
    await db_session.commit()
    await recommendation_service.mark_converted_for_order(db_session, order)
    await db_session.commit()

    opportunities = await recommendation_service.get_cross_sell_opportunities(
        db_session, s["a"].id
    )
    assert len(opportunities) == 1
    assert opportunities[0]["recommended_product_name"] == "Mouse Pad"
    assert opportunities[0]["revenue_generated"] == 199.0


# --- Capabilities + analytics HTTP routes ----------------------------------


@pytest.mark.asyncio
async def test_agent_capabilities_route(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert "buying_flow" in body
    assert "order_lifecycle" in body
    assert "PAID" in body["order_lifecycle"]


@pytest.mark.asyncio
async def test_merchant_analytics_route(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/merchant/analytics", params={"merchant_id": str(merchant.id)}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "revenue" in body
    assert "products" in body


@pytest.mark.asyncio
async def test_growth_campaign_routes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    merchant = await _merchant(db_session)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/growth/campaigns",
        json={
            "merchant_id": str(merchant.id),
            "campaign_type": "cross_sell_offer",
            "offer": {"discount_price": 149},
            "message": "Test campaign",
        },
    )
    assert resp.status_code == 201
    campaign_id = resp.json()["campaign_id"]
    assert resp.json()["status"] == "PENDING_APPROVAL"

    resp = await client.get(
        "/api/v1/growth/campaigns", params={"merchant_id": str(merchant.id)}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.post(
        f"/api/v1/growth/campaigns/{campaign_id}/approve", json={"approved_by": "owner"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "APPROVED"
