from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin


class TransactionPolicy(UUIDPKMixin, TimestampMixin, Base):
    """Deterministic, backend-enforced spending limits.

    A row with `buyer_id IS NULL` is the merchant's default policy applied
    to every buyer that doesn't have a more specific row. The policy
    engine (Phase 2) always resolves buyer-specific -> merchant-default,
    never trusts a value coming from the LLM or the request body.
    """

    __tablename__ = "transaction_policies"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    max_transaction_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    max_daily_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    confirmation_required: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    # Comma-separated for simplicity, e.g. "card,upi,netbanking". Empty = all Razorpay methods.
    allowed_payment_methods: Mapped[str] = mapped_column(
        String(255), default="", nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
