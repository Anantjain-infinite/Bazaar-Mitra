from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, UUIDPKMixin
from app.db.models.enums import ActorType


class AuditEvent(UUIDPKMixin, Base):
    """Append-only log of every consequential action in the system.

    This table is intentionally NOT foreign-keyed to the entities it
    references (merchant/buyer/order/etc.) — audit rows must remain even
    if the referenced row is later deleted, and writes to this table must
    never be blocked by an unrelated FK issue. IDs are stored as plain
    UUID columns instead.

    Never write secrets, card numbers, CVV, or credentials into
    `metadata_` or `explanation` — see audit_service (Phase 4) for the
    redaction guarantees around this table.
    """

    __tablename__ = "audit_events"

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    actor_type: Mapped[ActorType] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    buyer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    explanation: Mapped[str] = mapped_column(String(1000), nullable=False)
    policy_result: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # PASS / FAIL / N/A
    confirmation_state: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # EXPLICIT / NONE / N/A / BLOCKED_BY_POLICY

    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Free-form, non-sensitive extra context (e.g. razorpay order id, http status).
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
