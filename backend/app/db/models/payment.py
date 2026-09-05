from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin
from app.db.models.enums import PaymentStatus


class Payment(UUIDPKMixin, TimestampMixin, Base):
    """One row per payment attempt against an order.

    Design note: the spec describes `payments` and `payment_attempts` as
    separate tables, but every field it lists for `payment_attempts`
    (attempt number, Razorpay order id, status, failure reason,
    timestamps) is already a subset of the `payments` fields. Splitting
    them would mean writing the same row to two tables on every attempt
    with no independent lifecycle. Instead: a new `Payment` row is
    inserted for every attempt (never overwritten), ordered by
    `attempt_number`, which gives the same "full retry history, nothing
    overwritten" guarantee the spec asks for with one normalized table.
    `Order.payment_status` always mirrors the newest attempt's status.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "attempt_number", name="uq_payments_order_attempt"
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    razorpay_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    razorpay_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        String(24), default=PaymentStatus.CREATED, nullable=False
    )

    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Non-sensitive metadata only (e.g. method, bank, wallet) — never card
    # numbers/CVV/secrets. Enforced in payment_service before this is written.
    raw_response_metadata: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )

    # Idempotency key supplied by the caller when creating this attempt,
    # so a retried HTTP request can't create two Razorpay orders.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )

    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship(back_populates="payments")  # noqa: F821
