"""Merchant Growth Agent — tool functions (spec section 10D).

Merchant-facing only — every tool here requires `ctx.merchant_id` to be
set (a buyer session reaching this agent is exactly the handoff-failure
case demonstrated in handoff_service). Never exposes buyer-private
information beyond what's needed for aggregate metrics, and never
executes a campaign without a merchant's explicit approval — see
growth_service.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import AgentContext
from app.services import growth_service, recommendation_service


def _require_merchant(ctx: AgentContext) -> dict | None:
    if ctx.merchant_id is None:
        return {"ok": False, "error": "This agent requires a merchant-facing session"}
    return None


async def get_revenue_metrics(
    db: AsyncSession, ctx: AgentContext, days: int = 30
) -> dict:
    err = _require_merchant(ctx)
    if err is not None:
        return err
    metrics = await growth_service.get_revenue_metrics(db, ctx.merchant_id, days=days)
    return {"ok": True, **metrics}


async def get_product_metrics(
    db: AsyncSession, ctx: AgentContext, days: int = 30
) -> dict:
    err = _require_merchant(ctx)
    if err is not None:
        return err
    metrics = await growth_service.get_product_metrics(db, ctx.merchant_id, days=days)
    return {"ok": True, **metrics}


async def get_cross_sell_opportunities(db: AsyncSession, ctx: AgentContext) -> dict:
    err = _require_merchant(ctx)
    if err is not None:
        return err
    opportunities = await recommendation_service.get_cross_sell_opportunities(
        db, ctx.merchant_id
    )
    return {"ok": True, "opportunities": opportunities}


async def create_campaign_draft(
    db: AsyncSession,
    ctx: AgentContext,
    *,
    campaign_type: str,
    offer: dict,
    message: str,
    audience_definition: dict,
) -> dict:
    """Prepares a campaign for the merchant to review — always lands in
    PENDING_APPROVAL. See `approve_campaign`; nothing here can reach
    RUNNING on its own (spec section 23).
    """
    err = _require_merchant(ctx)
    if err is not None:
        return err
    campaign = await growth_service.create_campaign_draft(
        db,
        merchant_id=ctx.merchant_id,
        campaign_type=campaign_type,
        offer=offer,
        message=message,
        audience_definition=audience_definition,
    )
    await db.commit()
    return {
        "ok": True,
        "campaign_id": str(campaign.id),
        "status": campaign.status.value
        if hasattr(campaign.status, "value")
        else campaign.status,
        "message": "Campaign drafted — awaiting merchant approval before it can run.",
    }


async def approve_campaign(
    db: AsyncSession, ctx: AgentContext, campaign_id: uuid.UUID, approved_by: str
) -> dict:
    err = _require_merchant(ctx)
    if err is not None:
        return err
    try:
        campaign = await growth_service.approve_campaign(
            db, campaign_id, approved_by=approved_by
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if campaign is None:
        return {"ok": False, "error": f"No campaign found with id {campaign_id}"}
    await db.commit()
    return {"ok": True, "campaign_id": str(campaign.id), "status": "APPROVED"}


async def get_campaign_metrics(
    db: AsyncSession, ctx: AgentContext, campaign_id: uuid.UUID
) -> dict:
    del ctx  # not needed for this read, kept for tool-call signature symmetry
    metrics = await growth_service.get_campaign_metrics(db, campaign_id)
    if metrics is None:
        return {"ok": False, "error": f"No campaign found with id {campaign_id}"}
    return {"ok": True, **metrics}
