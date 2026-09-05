from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CreateCampaignRequest(BaseModel):
    merchant_id: uuid.UUID
    campaign_type: str
    offer: dict
    message: str
    audience_definition: dict = Field(default_factory=dict)


class ApproveCampaignRequest(BaseModel):
    approved_by: str


class ExecuteCampaignRequest(BaseModel):
    audience_buyer_ids: list[uuid.UUID] = Field(default_factory=list)
