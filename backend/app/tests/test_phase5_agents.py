"""Phase 5 tests — AgentContext persistence, handoff protocol, and the
specialist tool layers, exercised end-to-end through real service calls
(no LLM/voice involved — that's exactly the point: the business logic
underneath every tool call is fully testable without one).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import main_agent, order_support_agent, payments_agent, returns_agent
from app.agents.context import load_context, save_context
from app.db.models import (
    Buyer,
    Merchant,
    Product,
    ProductRelationship,
    TransactionPolicy,
)
from app.db.models.enums import OrderStatus
from app.integrations import razorpay as razorpay_integration
from app.services import handoff_service, session_service


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


# --- session_service ---------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_update_session(db_session: AsyncSession) -> None:
    buyer = await _buyer(db_session)
    await db_session.commit()

    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, channel="voice", language="hi"
    )
    await db_session.commit()

    assert session.current_agent == "main_agent"
    assert session.active is True

    await session_service.update_session(
        db_session, session.id, current_topic="wireless mouse"
    )
    await db_session.commit()

    fetched = await session_service.get_session(db_session, session.id)
    assert fetched.current_topic == "wireless mouse"


@pytest.mark.asyncio
async def test_update_session_rejects_unknown_field(db_session: AsyncSession) -> None:
    buyer = await _buyer(db_session)
    await db_session.commit()
    session = await session_service.create_session(db_session, buyer_id=buyer.id)
    await db_session.commit()

    with pytest.raises(ValueError, match="Unknown AgentSession field"):
        await session_service.update_session(
            db_session, session.id, not_a_real_field="x"
        )


# --- handoff_service -----------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_to_known_agent_succeeds_and_carries_context(
    db_session: AsyncSession,
) -> None:
    merchant, buyer, mouse, _pad = await _seeded_shop(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)
    await main_agent.add_to_cart(db_session, ctx, mouse.id, 1)
    await main_agent.checkout(db_session, ctx)  # gives ctx a real order_id

    handoff = await handoff_service.initiate_handoff(
        db_session,
        session.id,
        to_agent="payments_agent",
        reason="payment failed",
        conversation_summary="test",
    )
    await db_session.commit()

    assert handoff.success is True
    assert handoff.from_agent == "main_agent"
    assert handoff.to_agent == "payments_agent"
    assert handoff.shared_context["order_id"] == str(ctx.order_id)

    session = await session_service.get_session(db_session, session.id)
    assert session.current_agent == "payments_agent"
    assert session.previous_agent == "main_agent"


@pytest.mark.asyncio
async def test_handoff_to_unknown_agent_fails_cleanly(db_session: AsyncSession) -> None:
    buyer = await _buyer(db_session)
    await db_session.commit()
    session = await session_service.create_session(db_session, buyer_id=buyer.id)
    await db_session.commit()

    with pytest.raises(handoff_service.HandoffFailedError):
        await handoff_service.initiate_handoff(
            db_session, session.id, to_agent="nonexistent_agent", reason="test"
        )
    await db_session.commit()

    # Session must be unaffected by the failed handoff.
    session = await session_service.get_session(db_session, session.id)
    assert session.current_agent == "main_agent"

    history = await handoff_service.get_handoff_history(db_session, session.id)
    assert len(history) == 1
    assert history[0].success is False


@pytest.mark.asyncio
async def test_buyer_session_cannot_handoff_to_growth_agent(
    db_session: AsyncSession,
) -> None:
    """Growth agent is merchant-facing — a buyer session (no merchant_id
    set on the session itself in this scenario) hitting it demonstrates
    the handoff-failure recovery path from spec section 12."""
    buyer = await _buyer(db_session)
    await db_session.commit()
    session = await session_service.create_session(
        db_session, buyer_id=buyer.id
    )  # no merchant_id
    await db_session.commit()

    with pytest.raises(handoff_service.HandoffFailedError) as exc_info:
        await handoff_service.initiate_handoff(
            db_session, session.id, to_agent="growth_agent", reason="test"
        )
    assert "not available" in exc_info.value.reason


@pytest.mark.asyncio
async def test_return_to_previous_agent(db_session: AsyncSession) -> None:
    buyer = await _buyer(db_session)
    await db_session.commit()
    session = await session_service.create_session(db_session, buyer_id=buyer.id)
    await db_session.commit()

    await handoff_service.initiate_handoff(
        db_session, session.id, to_agent="returns_agent", reason="return item"
    )
    await db_session.commit()

    handoff = await handoff_service.return_to_previous_agent(db_session, session.id)
    await db_session.commit()

    assert handoff.from_agent == "returns_agent"
    assert handoff.to_agent == "main_agent"

    session = await session_service.get_session(db_session, session.id)
    assert session.current_agent == "main_agent"


# --- Full multi-agent flow through the tool layer -------------------------


async def _seeded_shop(db: AsyncSession):
    merchant = await _merchant(db)
    buyer = await _buyer(db)
    await _policy(
        db,
        merchant,
        max_transaction_amount=Decimal("1000"),
        max_daily_amount=Decimal("3000"),
    )
    mouse = Product(
        merchant_id=merchant.id,
        sku="MOUSE",
        name="Wireless Mouse",
        category="electronics",
        price=Decimal("799"),
        currency="INR",
        stock_quantity=23,
        active=True,
        metadata_={},
    )
    pad = Product(
        merchant_id=merchant.id,
        sku="PAD",
        name="Mouse Pad",
        category="electronics",
        price=Decimal("199"),
        currency="INR",
        stock_quantity=50,
        active=True,
        metadata_={},
    )
    db.add_all([mouse, pad])
    await db.flush()
    db.add(
        ProductRelationship(
            merchant_id=merchant.id,
            product_id=mouse.id,
            related_product_id=pad.id,
            relationship_type="CROSS_SELL",
            priority=10,
        )
    )
    await db.commit()
    return merchant, buyer, mouse, pad


@pytest.mark.asyncio
async def test_full_buyer_journey_through_main_agent_tools(
    db_session: AsyncSession,
) -> None:
    merchant, buyer, mouse, pad = await _seeded_shop(db_session)

    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    search_result = await main_agent.search_products(
        db_session, ctx, "wireless mouse in stock"
    )
    assert search_result["count"] >= 1

    add_result = await main_agent.add_to_cart(db_session, ctx, mouse.id, 1)
    assert add_result["ok"] is True

    rec_result = await main_agent.get_recommendations(db_session, ctx, mouse.id)
    assert rec_result["cross_sell"][0]["name"] == "Mouse Pad"

    add_result2 = await main_agent.add_to_cart(db_session, ctx, pad.id, 1)
    assert add_result2["cart_total"] == 998.0

    checkout_result = await main_agent.checkout(db_session, ctx)
    assert checkout_result["ok"] is True
    assert checkout_result["total"] == 998.0
    assert checkout_result["policy"]["allowed"] is True

    confirm_result = await main_agent.request_payment_confirmation_and_confirm(
        db_session, ctx
    )
    assert confirm_result["ok"] is True
    assert confirm_result["status"] == "CONFIRMED"

    with patch.object(
        razorpay_integration,
        "create_order",
        return_value={
            "id": "order_fake",
            "amount": 99800,
            "currency": "INR",
            "status": "created",
        },
    ):
        payment_result = await main_agent.initiate_payment(db_session, ctx)
    assert payment_result["ok"] is True
    assert payment_result["amount_paise"] == 99800

    # Context should have accumulated cart/order/payment ids across the whole flow.
    assert ctx.cart_id is not None
    assert ctx.order_id is not None
    assert ctx.payment_id is not None

    # And it's genuinely persisted — reload from a fresh context object.
    reloaded = await load_context(db_session, session.id)
    assert reloaded.order_id == ctx.order_id
    assert reloaded.payment_id == ctx.payment_id


@pytest.mark.asyncio
async def test_over_limit_checkout_blocked_before_payment(
    db_session: AsyncSession,
) -> None:
    merchant, buyer, _mouse, _pad = await _seeded_shop(db_session)
    pro = Product(
        merchant_id=merchant.id,
        sku="PRO",
        name="Pro Mouse",
        category="electronics",
        price=Decimal("1299"),
        currency="INR",
        stock_quantity=5,
        active=True,
        metadata_={},
    )
    db_session.add(pro)
    await db_session.commit()

    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, pro.id, 1)
    checkout_result = await main_agent.checkout(db_session, ctx)
    assert checkout_result["policy"]["allowed"] is False

    confirm_result = await main_agent.request_payment_confirmation_and_confirm(
        db_session, ctx
    )
    assert confirm_result["ok"] is False
    assert confirm_result["error"] == "policy_rejected"

    payment_result = await main_agent.initiate_payment(db_session, ctx)
    assert payment_result["ok"] is False
    assert (
        payment_result["error"] == "order_not_payable"
    )  # order never left PENDING_CONFIRMATION


@pytest.mark.asyncio
async def test_handoff_to_payments_specialist_and_back(
    db_session: AsyncSession,
) -> None:
    merchant, buyer, mouse, _pad = await _seeded_shop(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, mouse.id, 1)
    await main_agent.checkout(db_session, ctx)
    await main_agent.request_payment_confirmation_and_confirm(db_session, ctx)
    with patch.object(
        razorpay_integration,
        "create_order",
        return_value={
            "id": "order_fake2",
            "amount": 79900,
            "currency": "INR",
            "status": "created",
        },
    ):
        await main_agent.initiate_payment(db_session, ctx)

    handoff_result = await main_agent.handoff_to_payments(
        db_session, ctx, reason="payment failed, need retry"
    )
    assert handoff_result["ok"] is True
    assert ctx.current_agent == "payments_agent"

    # The payments specialist picks up the SAME context (order/payment ids
    # carried across the handoff) without the caller repeating anything.
    status = await payments_agent.get_payment_status(db_session, ctx)
    assert status["ok"] is True
    assert status["payment_id"] == str(ctx.payment_id)

    history = await payments_agent.get_payment_history(db_session, ctx)
    assert history["ok"] is True
    assert len(history["attempts"]) == 1

    return_result = await payments_agent.return_to_main_agent(db_session, ctx)
    assert return_result["ok"] is True
    assert ctx.current_agent == "main_agent"


@pytest.mark.asyncio
async def test_returns_specialist_eligibility_and_request(
    db_session: AsyncSession,
) -> None:
    merchant, buyer, mouse, _pad = await _seeded_shop(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, mouse.id, 1)
    await main_agent.checkout(db_session, ctx)
    await main_agent.request_payment_confirmation_and_confirm(db_session, ctx)

    # Manually mark the order PAID (bypassing full payment flow) to reach
    # a return-eligible state — payment flow itself is Phase 3's concern.
    from app.services import order_service

    order = await order_service.get_order(db_session, ctx.order_id)
    order.status = OrderStatus.PAID
    order.payment_status = "CAPTURED"
    await db_session.commit()

    policy = await returns_agent.get_return_policy(db_session, ctx)
    assert policy["window_days"] == 7

    await main_agent.handoff_to_returns(
        db_session, ctx, reason="wants to return the mouse"
    )
    assert ctx.current_agent == "returns_agent"

    eligibility = await returns_agent.check_return_eligibility(
        db_session, ctx, mouse.id
    )
    assert eligibility["eligible"] is True

    request_result = await returns_agent.create_return_request(
        db_session, ctx, mouse.id, "changed my mind"
    )
    assert request_result["ok"] is True
    assert request_result["eligible"] is True

    # A second request for the same item should now be rejected as a duplicate.
    eligibility2 = await returns_agent.check_return_eligibility(
        db_session, ctx, mouse.id
    )
    assert eligibility2["eligible"] is False


@pytest.mark.asyncio
async def test_order_support_cancel_before_payment(db_session: AsyncSession) -> None:
    merchant, buyer, mouse, _pad = await _seeded_shop(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    await main_agent.add_to_cart(db_session, ctx, mouse.id, 1)
    await main_agent.checkout(db_session, ctx)

    await main_agent.handoff_to_order_support(db_session, ctx, reason="wants to cancel")
    status = await order_support_agent.get_order_status(db_session, ctx)
    assert status["status"] == "PENDING_CONFIRMATION"

    cancel_result = await order_support_agent.cancel_order(
        db_session, ctx, reason="changed mind"
    )
    assert cancel_result["ok"] is True
    assert cancel_result["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_handoff_to_growth_from_buyer_session_fails_gracefully(
    db_session: AsyncSession,
) -> None:
    _merchant, buyer, _mouse, _pad = await _seeded_shop(db_session)
    # Session deliberately has no merchant_id, simulating a pure buyer session.
    session = await session_service.create_session(db_session, buyer_id=buyer.id)
    await db_session.commit()
    ctx = await load_context(db_session, session.id)

    result = await main_agent.handoff_to_growth(
        db_session, ctx, reason="how's my store doing"
    )
    assert result["ok"] is False
    assert result["error"] == "handoff_failed"
    # Session must remain usable — still on main_agent, not stuck.
    assert ctx.current_agent == "main_agent"


@pytest.mark.asyncio
async def test_context_survives_reload_across_simulated_new_agent_instance(
    db_session: AsyncSession,
) -> None:
    """Simulates exactly the scenario the spec's CallState design note
    describes: a handoff creates a new agent instance with a blank
    Python object, so state must come from the DB, not from `self`."""
    merchant, buyer, mouse, _pad = await _seeded_shop(db_session)
    session = await session_service.create_session(
        db_session, buyer_id=buyer.id, merchant_id=merchant.id
    )
    await db_session.commit()

    ctx1 = await load_context(db_session, session.id)
    await main_agent.add_to_cart(db_session, ctx1, mouse.id, 2)
    ctx1.current_topic = "buying a mouse"
    await save_context(db_session, ctx1)

    # Simulate a brand new agent instance (e.g. after a handoff) loading
    # fresh context from nothing but the session id.
    ctx2 = await load_context(db_session, session.id)
    assert ctx2.cart_id == ctx1.cart_id
    assert ctx2.current_topic == "buying a mouse"

    cart_view = await main_agent.view_cart(db_session, ctx2)
    assert cart_view["total"] == 1598.0
