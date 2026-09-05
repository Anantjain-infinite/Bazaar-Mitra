from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import catalog_service, growth_service, recommendation_service

router = APIRouter(prefix="/api/v1/merchant", tags=["merchant-analytics"])


async def _require_merchant(merchant_id: uuid.UUID, db: AsyncSession):
    merchant = await catalog_service.get_merchant(db, merchant_id)
    if merchant is None:
        raise HTTPException(
            status_code=404, detail=f"No merchant found with id {merchant_id}"
        )
    return merchant


@router.get("/analytics")
async def merchant_analytics(
    merchant_id: uuid.UUID = Query(...),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Combined revenue + product overview for the merchant dashboard's
    Overview tab (spec section 24).
    """
    await _require_merchant(merchant_id, db)
    revenue = await growth_service.get_revenue_metrics(db, merchant_id, days=days)
    products = await growth_service.get_product_metrics(db, merchant_id, days=days)
    return {"revenue": revenue, "products": products}


@router.get("/revenue")
async def merchant_revenue(
    merchant_id: uuid.UUID = Query(...),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_merchant(merchant_id, db)
    return await growth_service.get_revenue_metrics(db, merchant_id, days=days)


@router.get("/recommendations")
async def merchant_recommendations(
    merchant_id: uuid.UUID = Query(...),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """AI recommendation performance + ranked cross-sell opportunities —
    the data behind spec's "6 prior recommendations converted, estimated
    opportunity ₹X" growth-agent example.
    """
    await _require_merchant(merchant_id, db)
    metrics = await recommendation_service.get_recommendation_metrics(
        db, merchant_id, days=days
    )
    opportunities = await recommendation_service.get_cross_sell_opportunities(
        db, merchant_id
    )
    return {"metrics": metrics, "opportunities": opportunities}
