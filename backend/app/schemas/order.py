from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.policy import PolicyCheckResult


class CreateOrderRequest(BaseModel):
    cart_id: uuid.UUID
    # Set to true only after the buyer has been shown a changed price/total
    # and explicitly re-confirmed it — see order_service.create_order_from_cart.
    acknowledge_price_change: bool = False


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    name_snapshot: str
    quantity: int
    quoted_unit_price: float
    final_unit_price: float
    total: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    public_order_id: str
    merchant_id: uuid.UUID
    buyer_id: uuid.UUID
    cart_id: uuid.UUID | None
    status: str
    payment_status: str
    currency: str
    subtotal: float
    discount: float
    shipping_amount: float
    total: float
    confirmation_required: bool
    confirmation_received: bool
    confirmed_at: datetime | None
    items: list[OrderItemOut] = Field(default_factory=list)


class OrderIssue(BaseModel):
    """One item-level problem found while validating a cart into an
    order — surfaced so the caller can explain exactly what changed
    rather than getting an opaque failure.
    """

    product_id: uuid.UUID
    product_name: str
    issue: str  # "price_changed" | "out_of_stock" | "product_unavailable"
    quoted_price: float | None = None
    current_price: float | None = None
    requested_quantity: int | None = None
    available_stock: int | None = None
    message: str


class CreateOrderResponse(BaseModel):
    order: OrderOut
    policy: PolicyCheckResult


class ConfirmOrderResponse(BaseModel):
    order: OrderOut
    policy: PolicyCheckResult
