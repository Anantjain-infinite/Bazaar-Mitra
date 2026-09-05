from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class CreateCartRequest(BaseModel):
    merchant_id: uuid.UUID
    buyer_id: uuid.UUID


class AddCartItemRequest(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(default=1, ge=1)


class UpdateCartItemRequest(BaseModel):
    quantity: int = Field(..., ge=1)


class CartItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    unit_price: float
    line_total: float


class CartItemDetailOut(CartItemOut):
    """Cart item enriched with the current live product info, so a caller
    can see at a glance whether the quoted price has drifted from the
    live price without a separate lookup.
    """

    product_name: str
    product_sku: str
    current_price: float
    current_stock: int
    price_drifted: bool


class CartOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    buyer_id: uuid.UUID
    status: str
    currency: str
    subtotal: float
    discount: float
    total: float
    items: list[CartItemOut] = Field(default_factory=list)
