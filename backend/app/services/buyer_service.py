"""Buyer service — minimal CRUD plus the get-or-create bridge that lets a
voice caller's phone number (or any other channel's stable identity)
become a real `Buyer` row the rest of the commerce backend can use.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Buyer


async def get_buyer(db: AsyncSession, buyer_id: uuid.UUID) -> Buyer | None:
    return await db.get(Buyer, buyer_id)


async def get_or_create_buyer_by_phone(
    db: AsyncSession,
    phone: str,
    *,
    name: str | None = None,
    preferred_language: str = "en",
) -> Buyer:
    stmt = select(Buyer).where(Buyer.phone == phone)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if name and not existing.name:
            existing.name = name
            await db.flush()
        return existing

    buyer = Buyer(
        name=name or f"Caller {phone[-4:]}",
        phone=phone,
        preferred_language=preferred_language,
        is_ai_agent=False,
        consent_flags={},
    )
    db.add(buyer)
    await db.flush()
    return buyer
