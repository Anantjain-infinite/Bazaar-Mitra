from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_name: str
    owner_name: str
    phone: str
    email: str | None
    address: str | None
    city: str
    state: str
    preferred_language: str
    currency: str
    active: bool


class MerchantSummary(BaseModel):
    """Lightweight merchant reference embedded in search/comparison results."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_name: str
    city: str
    state: str
