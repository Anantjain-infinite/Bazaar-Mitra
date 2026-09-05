from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.growth import (
    ApproveCampaignRequest,
    CreateCampaignRequest,
    ExecuteCampaignRequest,
)
from app.services import growth_service

router = APIRouter(prefix="/api/v1/growth", tags=["growth"])


@router.post("/campaigns", status_code=201)
async def create_campaign(
    body: CreateCampaignRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Always creates a campaign in PENDING_APPROVAL — see
    growth_service module docstring for why nothing here can reach
    RUNNING without a separate, explicit approval call.
    """
    campaign = await growth_service.create_campaign_draft(
        db,
        merchant_id=body.merchant_id,
        campaign_type=body.campaign_type,
        offer=body.offer,
        message=body.message,
        audience_definition=body.audience_definition,
    )
    await db.commit()
    return {
        "campaign_id": str(campaign.id),
        "status": campaign.status.value
        if hasattr(campaign.status, "value")
        else campaign.status,
    }


@router.get("/campaigns")
async def list_campaigns(
    merchant_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    campaigns = await growth_service.list_campaigns(db, merchant_id)
    return [
        {
            "campaign_id": str(c.id),
            "campaign_type": c.campaign_type,
            "status": c.status.value if hasattr(c.status, "value") else c.status,
            "offer": c.offer,
            "message": c.message,
            "created_at": c.created_at.isoformat(),
        }
        for c in campaigns
    ]


@router.post("/campaigns/{campaign_id}/approve")
async def approve_campaign(
    campaign_id: uuid.UUID,
    body: ApproveCampaignRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        campaign = await growth_service.approve_campaign(
            db, campaign_id, approved_by=body.approved_by
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if campaign is None:
        raise HTTPException(
            status_code=404, detail=f"No campaign found with id {campaign_id}"
        )
    await db.commit()
    return {"campaign_id": str(campaign.id), "status": "APPROVED"}


@router.post("/campaigns/{campaign_id}/reject")
async def reject_campaign(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    campaign = await growth_service.reject_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=404, detail=f"No campaign found with id {campaign_id}"
        )
    await db.commit()
    return {"campaign_id": str(campaign.id), "status": "REJECTED"}


@router.post("/campaigns/{campaign_id}/execute")
async def execute_campaign(
    campaign_id: uuid.UUID,
    body: ExecuteCampaignRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Only callable after approval — see growth_service.execute_campaign
    for the explicit scope note on what "execute" does and doesn't do
    (no real message delivery is wired up).
    """
    try:
        campaign = await growth_service.execute_campaign(
            db, campaign_id, audience_buyer_ids=body.audience_buyer_ids
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if campaign is None:
        raise HTTPException(
            status_code=404, detail=f"No campaign found with id {campaign_id}"
        )
    await db.commit()
    return {
        "campaign_id": str(campaign.id),
        "status": "RUNNING",
        "audience_size": len(body.audience_buyer_ids),
    }


@router.get("/campaigns/{campaign_id}/metrics")
async def campaign_metrics(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    metrics = await growth_service.get_campaign_metrics(db, campaign_id)
    if metrics is None:
        raise HTTPException(
            status_code=404, detail=f"No campaign found with id {campaign_id}"
        )
    return metrics
