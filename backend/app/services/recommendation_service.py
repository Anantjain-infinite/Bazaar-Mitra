"""Recommendation service (spec section 21).

Tracks every upsell/cross-sell suggestion an agent surfaces through its
full lifecycle: shown -> accepted/rejected -> converted (only if the
recommended product ends up in a PAID order). This is the data
`growth_service`'s "AI-assisted revenue" and "upsell conversion" metrics
are built from — nothing in Phase 8 invents those numbers, they're a
straight aggregation over `agent_recommendations` rows written here.

Recommendations never change the buyer's order on their own — see
main_agent.get_recommendations (surfaces them) and main_agent.add_to_cart
(the only thing that can accept one, by the buyer actually adding the
product), matching spec section 21: "Never let recommendations
automatically change the user's order."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRecommendation, Order


async def create_recommendation(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    product_id: uuid.UUID,
    source_product_id: uuid.UUID | None,
    recommendation_type: str,
    rationale: str,
    source_signal: str,
    confidence: float,
    cart_id: uuid.UUID | None = None,
) -> AgentRecommendation:
    rec = AgentRecommendation(
        merchant_id=merchant_id,
        cart_id=cart_id,
        product_id=product_id,
        source_product_id=source_product_id,
        recommendation_type=recommendation_type,
        rationale=rationale,
        source_signal=source_signal,
        confidence=Decimal(str(confidence)),
        accepted=None,
        converted=False,
        revenue_impact=Decimal("0"),
    )
    db.add(rec)
    await db.flush()
    return rec


async def mark_accepted(
    db: AsyncSession, recommendation_id: uuid.UUID, *, order_id: uuid.UUID | None = None
) -> AgentRecommendation | None:
    rec = await db.get(AgentRecommendation, recommendation_id)
    if rec is None:
        return None
    rec.accepted = True
    if order_id:
        rec.order_id = order_id
    await db.flush()
    return rec


async def mark_rejected(
    db: AsyncSession, recommendation_id: uuid.UUID
) -> AgentRecommendation | None:
    rec = await db.get(AgentRecommendation, recommendation_id)
    if rec is None:
        return None
    rec.accepted = False
    await db.flush()
    return rec


async def mark_converted_for_order(
    db: AsyncSession, order: Order
) -> list[AgentRecommendation]:
    """Called when an order becomes PAID (see payment_service's webhook
    handling). Any accepted-but-not-yet-converted recommendation whose
    product is actually in this order is marked converted, with revenue
    impact set to that line item's total — never a guessed number.
    """
    if order.cart_id is None:
        return []

    stmt = select(AgentRecommendation).where(
        AgentRecommendation.cart_id == order.cart_id,
        AgentRecommendation.accepted.is_(True),
        AgentRecommendation.converted.is_(False),
    )
    recs = (await db.execute(stmt)).scalars().all()
    if not recs:
        return []

    item_totals = {item.product_id: item.total for item in order.items}
    converted = []
    for rec in recs:
        if rec.product_id in item_totals:
            rec.converted = True
            rec.order_id = order.id
            rec.revenue_impact = item_totals[rec.product_id]
            converted.append(rec)
    if converted:
        await db.flush()
    return converted


async def get_recommendation_metrics(
    db: AsyncSession, merchant_id: uuid.UUID, *, days: int = 30
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = select(AgentRecommendation).where(
        AgentRecommendation.merchant_id == merchant_id,
        AgentRecommendation.created_at >= since,
    )
    recs = (await db.execute(stmt)).scalars().all()

    shown = len(recs)
    accepted = sum(1 for r in recs if r.accepted is True)
    rejected = sum(1 for r in recs if r.accepted is False)
    converted = sum(1 for r in recs if r.converted)
    revenue = sum((r.revenue_impact for r in recs if r.converted), Decimal("0"))

    return {
        "window_days": days,
        "recommendations_shown": shown,
        "accepted": accepted,
        "rejected": rejected,
        "converted": converted,
        "acceptance_rate": round(accepted / shown, 3) if shown else 0.0,
        "conversion_rate": round(converted / accepted, 3) if accepted else 0.0,
        "ai_assisted_revenue": float(revenue),
    }


async def get_cross_sell_opportunities(
    db: AsyncSession, merchant_id: uuid.UUID, *, limit: int = 5
) -> list[dict]:
    """Rank (source_product -> recommended_product) pairs by how much
    converted revenue they've generated, for the growth agent to suggest
    as campaign material. Only pairs with at least one acceptance are
    considered "opportunities" — untested pairs have no signal yet.
    """
    stmt = select(AgentRecommendation).where(
        AgentRecommendation.merchant_id == merchant_id,
        AgentRecommendation.source_product_id.is_not(None),
    )
    recs = (await db.execute(stmt)).scalars().all()

    from app.db.models import Product

    pairs: dict[tuple[uuid.UUID, uuid.UUID], dict] = {}
    for r in recs:
        key = (r.source_product_id, r.product_id)
        bucket = pairs.setdefault(
            key, {"shown": 0, "accepted": 0, "converted": 0, "revenue": Decimal("0")}
        )
        bucket["shown"] += 1
        if r.accepted:
            bucket["accepted"] += 1
        if r.converted:
            bucket["converted"] += 1
            bucket["revenue"] += r.revenue_impact

    opportunities = []
    for (source_id, target_id), stats in pairs.items():
        if stats["accepted"] == 0:
            continue
        source = await db.get(Product, source_id)
        target = await db.get(Product, target_id)
        if source is None or target is None:
            continue
        opportunities.append(
            {
                "source_product_id": str(source_id),
                "source_product_name": source.name,
                "recommended_product_id": str(target_id),
                "recommended_product_name": target.name,
                "recommended_product_price": float(target.price),
                "times_shown": stats["shown"],
                "times_accepted": stats["accepted"],
                "times_converted": stats["converted"],
                "conversion_rate": round(stats["converted"] / stats["accepted"], 3),
                "revenue_generated": float(stats["revenue"]),
            }
        )

    opportunities.sort(key=lambda o: o["revenue_generated"], reverse=True)
    return opportunities[:limit]
