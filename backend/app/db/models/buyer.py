from __future__ import annotations

from sqlalchemy import JSON, Boolean, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin


class Buyer(UUIDPKMixin, TimestampMixin, Base):
    """A human or AI-buyer principal that can place orders.

    AI buyers are represented as real Buyer rows too (with
    `is_ai_agent=True`) so that policy limits, audit trails, and order
    history work identically for human and agentic purchasing.
    """

    __tablename__ = "buyers"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_language: Mapped[str] = mapped_column(
        String(16), default="en", nullable=False
    )
    is_ai_agent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Consent / privacy flags, e.g. {"marketing": true, "recorded_calls": true}
    consent_flags: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Per-buyer overrides; NULL means "use the merchant's default policy".
    max_transaction_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    max_daily_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Buyer {self.name} ai={self.is_ai_agent}>"
