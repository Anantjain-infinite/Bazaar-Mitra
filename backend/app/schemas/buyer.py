from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class IdentifyBuyerRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=32)
    name: str | None = None
    preferred_language: str = "en"


class BuyerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    preferred_language: str
    is_ai_agent: bool
