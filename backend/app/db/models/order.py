from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin
from app.db.models.enums import OrderStatus


class Order(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    # Human-friendly, externally-shown ID (e.g. "ORD-A1B2C3") distinct from
    # the internal UUID primary key.
    public_order_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=False, index=True
    )
    cart_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id"), nullable=True
    )

    status: Mapped[OrderStatus] = mapped_column(
        String(24), default=OrderStatus.DRAFT, nullable=False
    )
    # payment_status mirrors the latest Payment.status for fast reads without a join.
    payment_status: Mapped[str] = mapped_column(
        String(24), default="NONE", nullable=False
    )

    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    shipping_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), default=0, nullable=False
    )
    total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    confirmation_required: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    confirmation_received: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    confirmed_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    payments: Mapped[list[Payment]] = relationship(  # noqa: F821
        back_populates="order", lazy="selectin"
    )


class OrderItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Price quoted when added to cart vs. the price actually charged.
    # These can legitimately differ only if the buyer re-confirmed a changed price.
    quoted_unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    final_unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
