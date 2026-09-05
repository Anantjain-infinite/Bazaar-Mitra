from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CreateAgentSessionRequest(BaseModel):
    buyer_id: uuid.UUID
    merchant_id: uuid.UUID | None = None
    channel: str = Field(default="api", description='e.g. "voice", "api", "text"')
    language: str = "en"


class AgentSessionOut(BaseModel):
    session_id: uuid.UUID
    current_agent: str
    previous_agent: str | None
    merchant_id: uuid.UUID | None
    buyer_id: uuid.UUID | None
    cart_id: uuid.UUID | None
    order_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    language: str


class AgentSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class AgentAddToCartRequest(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(default=1, ge=1)


class AgentCheckoutRequest(BaseModel):
    acknowledge_price_change: bool = False


class AgentBuyBestAvailableRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    city: str | None = None
