from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.buyer import BuyerOut, IdentifyBuyerRequest
from app.services import buyer_service

router = APIRouter(prefix="/api/v1/buyers", tags=["buyers"])


@router.post("/identify", response_model=BuyerOut)
async def identify_buyer(
    body: IdentifyBuyerRequest, db: AsyncSession = Depends(get_db)
) -> BuyerOut:
    """Get-or-create a Buyer by phone number - the identity bridge any
    channel (this demo frontend, the voice agent, a future integration)
    uses to turn "someone with this phone number" into a real Buyer row
    the rest of the commerce backend can operate on.
    """
    buyer = await buyer_service.get_or_create_buyer_by_phone(
        db, body.phone, name=body.name, preferred_language=body.preferred_language
    )
    await db.commit()
    return BuyerOut.model_validate(buyer)


@router.get("/{buyer_id}", response_model=BuyerOut)
async def get_buyer(
    buyer_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> BuyerOut:
    buyer = await buyer_service.get_buyer(db, buyer_id)
    if buyer is None:
        raise HTTPException(
            status_code=404, detail=f"No buyer found with id {buyer_id}"
        )
    return BuyerOut.model_validate(buyer)
