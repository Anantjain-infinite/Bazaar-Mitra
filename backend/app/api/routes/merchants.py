from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.merchant import MerchantOut
from app.services import catalog_service

router = APIRouter(prefix="/api/v1/merchants", tags=["merchants"])


@router.get("", response_model=list[MerchantOut])
async def list_merchants(
    city: str | None = Query(
        default=None, description="Filter by city, case-insensitive exact match"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[MerchantOut]:
    merchants = await catalog_service.list_merchants(
        db, city=city, limit=limit, offset=offset
    )
    return [MerchantOut.model_validate(m) for m in merchants]


@router.get("/{merchant_id}", response_model=MerchantOut)
async def get_merchant(
    merchant_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> MerchantOut:
    merchant = await catalog_service.get_merchant(db, merchant_id)
    if merchant is None:
        raise HTTPException(
            status_code=404, detail=f"No merchant found with id {merchant_id}"
        )
    return MerchantOut.model_validate(merchant)
