from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.order import OrderOut


class CreatePaymentRequest(BaseModel):
    order_id: uuid.UUID
    # Caller-supplied idempotency key — repeating the same key returns the
    # existing attempt instead of creating a second Razorpay order.
    idempotency_key: str | None = Field(default=None, max_length=128)


class RetryPaymentRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=128)


class VerifyPaymentRequest(BaseModel):
    payment_id: uuid.UUID
    razorpay_payment_id: str
    razorpay_signature: str


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    attempt_number: int
    razorpay_order_id: str | None
    razorpay_payment_id: str | None
    status: str
    amount: float
    currency: str
    failure_code: str | None
    failure_reason: str | None
    verified_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, payment) -> PaymentOut:
        return cls.model_validate(payment)


class CreatePaymentResponse(BaseModel):
    """Everything the frontend needs to open Razorpay Checkout — the
    public Key ID (never the secret), the Razorpay order id, and the
    exact amount in paise Checkout expects.
    """

    payment: PaymentOut
    razorpay_order_id: str
    razorpay_key_id: str
    amount_paise: int
    currency: str


class VerifyPaymentResponse(BaseModel):
    payment: PaymentOut
    order: OrderOut
    verified: bool


class OrderWithPaymentsOut(OrderOut):
    """The full order read, including its payment-attempt history — this
    is what GET /api/v1/orders/{id} returns, so a single call shows the
    "Attempt #1 FAILED, Attempt #2 CAPTURED" story the dashboard needs.
    """

    payments: list[PaymentOut] = Field(default_factory=list)
