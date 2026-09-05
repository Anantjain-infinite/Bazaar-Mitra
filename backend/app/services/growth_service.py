"""Growth service — merchant-facing revenue analytics and the campaign
approval pipeline (spec sections 22-23).

Every number here is aggregated directly from orders/payments/
agent_recommendations at read time — nothing is pre-computed or cached
in a way that could go stale, and nothing is estimated. Campaigns
created here always start in PENDING_APPROVAL and can never reach
RUNNING without an explicit `approve_campaign` call recording who
approved it — see spec section 23: "Never allow an LLM to spam
customers autonomously."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, CampaignEvent, Order, OrderItem, Payment, Product
from app.db.models.enums import CampaignStatus, OrderStatus, PaymentStatus
from app.services import recommendation_service


async def get_revenue_metrics(
    db: AsyncSession, merchant_id: uuid.UUID, *, days: int = 30
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)

    paid_stmt = select(Order).where(
        Order.merchant_id == merchant_id,
        Order.status == OrderStatus.PAID,
        Order.created_at >= since,
    )
    paid_orders = (await db.execute(paid_stmt)).scalars().all()

    total_revenue = sum((o.total for o in paid_orders), Decimal("0"))
    order_count = len(paid_orders)
    aov = (total_revenue / order_count) if order_count else Decimal("0")

    attempts_stmt = (
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(Order.merchant_id == merchant_id, Payment.created_at >= since)
    )
    attempts = (await db.execute(attempts_stmt)).scalars().all()
    captured = sum(1 for p in attempts if p.status == PaymentStatus.CAPTURED)
    total_attempts = len(attempts)
    payment_success_rate = (
        round(captured / total_attempts, 3) if total_attempts else 0.0
    )

    rec_metrics = await recommendation_service.get_recommendation_metrics(
        db, merchant_id, days=days
    )

    return {
        "window_days": days,
        "revenue": float(total_revenue),
        "orders": order_count,
        "average_order_value": float(aov),
        "payment_attempts": total_attempts,
        "payment_success_rate": payment_success_rate,
        "ai_assisted_orders": rec_metrics["converted"],
        "ai_assisted_revenue": rec_metrics["ai_assisted_revenue"],
        "upsell_conversions": rec_metrics["converted"],
    }


async def get_product_metrics(
    db: AsyncSession, merchant_id: uuid.UUID, *, days: int = 30, top_n: int = 5
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)

    stmt = (
        select(
            OrderItem.product_id,
            func.sum(OrderItem.total).label("revenue"),
            func.sum(OrderItem.quantity).label("units"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.merchant_id == merchant_id,
            Order.status == OrderStatus.PAID,
            Order.created_at >= since,
        )
        .group_by(OrderItem.product_id)
        .order_by(func.sum(OrderItem.total).desc())
    )
    rows = (await db.execute(stmt)).all()

    sold_product_ids = {row.product_id for row in rows}
    top_products = []
    for row in rows[:top_n]:
        product = await db.get(Product, row.product_id)
        if product is None:
            continue
        top_products.append(
            {
                "product_id": str(product.id),
                "name": product.name,
                "revenue": float(row.revenue),
                "units_sold": int(row.units),
            }
        )

    all_products_stmt = select(Product).where(
        Product.merchant_id == merchant_id, Product.active.is_(True)
    )
    all_products = (await db.execute(all_products_stmt)).scalars().all()
    slow_products = [
        {"product_id": str(p.id), "name": p.name, "stock_quantity": p.stock_quantity}
        for p in all_products
        if p.id not in sold_product_ids
    ][:top_n]

    return {
        "window_days": days,
        "top_products": top_products,
        "slow_products": slow_products,
    }


async def create_campaign_draft(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    campaign_type: str,
    offer: dict,
    message: str,
    audience_definition: dict,
    created_by_agent: str = "growth_agent",
) -> Campaign:
    campaign = Campaign(
        merchant_id=merchant_id,
        created_by_agent=created_by_agent,
        campaign_type=campaign_type,
        audience_definition=audience_definition,
        offer=offer,
        message=message,
        status=CampaignStatus.PENDING_APPROVAL,
    )
    db.add(campaign)
    await db.flush()
    return campaign


async def approve_campaign(
    db: AsyncSession, campaign_id: uuid.UUID, *, approved_by: str
) -> Campaign | None:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    if campaign.status != CampaignStatus.PENDING_APPROVAL:
        raise ValueError(
            f"Campaign {campaign_id} is {campaign.status}, not awaiting approval"
        )
    campaign.status = CampaignStatus.APPROVED
    campaign.approved_by = approved_by
    campaign.approved_at = datetime.now(UTC)
    await db.flush()
    return campaign


async def reject_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign | None:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    campaign.status = CampaignStatus.REJECTED
    await db.flush()
    return campaign


async def execute_campaign(
    db: AsyncSession, campaign_id: uuid.UUID, *, audience_buyer_ids: list[uuid.UUID]
) -> Campaign | None:
    """Transition an APPROVED campaign to RUNNING and record targeting/
    send events for the given audience.

    IMPORTANT scope note: this records the decisioning/tracking pipeline
    (who was targeted, marked "sent") — it does NOT call any real SMS/
    email/push provider. No messaging credentials are part of this
    project's env vars, and actually delivering messages is outside
    this spec's Tier 1/2 requirements; only the approval-gated pipeline
    and its conversion tracking are in scope.
    """
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    if campaign.status != CampaignStatus.APPROVED:
        raise ValueError(
            f"Campaign {campaign_id} is {campaign.status}, must be APPROVED before it can run"
        )

    campaign.status = CampaignStatus.RUNNING
    for buyer_id in audience_buyer_ids:
        db.add(
            CampaignEvent(
                campaign_id=campaign.id, buyer_id=buyer_id, targeted=True, sent=True
            )
        )
    await db.flush()
    return campaign


async def get_campaign_metrics(db: AsyncSession, campaign_id: uuid.UUID) -> dict | None:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    stmt = select(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id)
    events = (await db.execute(stmt)).scalars().all()
    return {
        "campaign_id": str(campaign.id),
        "status": campaign.status.value
        if hasattr(campaign.status, "value")
        else campaign.status,
        "targeted": sum(1 for e in events if e.targeted),
        "sent": sum(1 for e in events if e.sent),
        "opened": sum(1 for e in events if e.opened),
        "clicked": sum(1 for e in events if e.clicked),
        "converted": sum(1 for e in events if e.converted),
        "revenue_generated": float(
            sum((e.revenue_generated for e in events), Decimal("0"))
        ),
    }


async def list_campaigns(db: AsyncSession, merchant_id: uuid.UUID) -> list[Campaign]:
    stmt = (
        select(Campaign)
        .where(Campaign.merchant_id == merchant_id)
        .order_by(Campaign.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
