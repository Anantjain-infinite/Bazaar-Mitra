"""Enum types shared across models.

Kept as plain Python `str` enums (not native Postgres ENUM types) so that
adding a new status later is a simple code change + migration, rather than
an `ALTER TYPE`. Columns store these as VARCHAR with a CHECK constraint
where it matters.
"""

from __future__ import annotations

import enum


class OrderStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"


class CartStatus(str, enum.Enum):
    OPEN = "OPEN"
    LOCKED = "LOCKED"  # locked while checkout/payment is in progress
    CONVERTED = "CONVERTED"  # became an order
    ABANDONED = "ABANDONED"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"
    CHECKOUT_OPENED = "CHECKOUT_OPENED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class RelationshipType(str, enum.Enum):
    FREQUENTLY_BOUGHT_TOGETHER = "FREQUENTLY_BOUGHT_TOGETHER"
    UPSELL = "UPSELL"
    CROSS_SELL = "CROSS_SELL"
    ALTERNATIVE = "ALTERNATIVE"
    BUNDLE = "BUNDLE"
    ACCESSORY = "ACCESSORY"


class ActorType(str, enum.Enum):
    BUYER = "BUYER"
    MERCHANT = "MERCHANT"
    MAIN_AGENT = "MAIN_AGENT"
    PAYMENTS_AGENT = "PAYMENTS_AGENT"
    RETURNS_AGENT = "RETURNS_AGENT"
    ORDER_SUPPORT_AGENT = "ORDER_SUPPORT_AGENT"
    GROWTH_AGENT = "GROWTH_AGENT"
    BUYER_AGENT = "BUYER_AGENT"
    SYSTEM = "SYSTEM"
    WEBHOOK = "WEBHOOK"


class ReturnStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ITEM_RECEIVED = "ITEM_RECEIVED"
    COMPLETED = "COMPLETED"


class RefundStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class CampaignStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
