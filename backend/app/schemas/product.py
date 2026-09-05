from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, computed_field


class ProductOut(BaseModel):
    """Plain product representation for human-facing REST consumers
    (the merchant dashboard). For the richer agent shape with
    related/upsell/cross-sell products, see AgentProduct in
    app.schemas.catalog.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    sku: str
    name: str
    description: str | None
    category: str
    price: float
    currency: str
    stock_quantity: int
    active: bool

    @computed_field
    @property
    def available(self) -> bool:
        return self.active and self.stock_quantity > 0
