from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin


class AgentSession(UUIDPKMixin, TimestampMixin, Base):
    """The durable record backing `AgentContext` (Phase 5).

    This is the single source of truth for "what agent is this session
    with right now" — deliberately NOT kept only on an in-memory agent
    instance, because LiveKit handoffs create a *new* agent instance and
    anything living only on `self` would be lost.
    """

    __tablename__ = "agent_sessions"

    buyer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=True
    )
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=True
    )

    channel: Mapped[str] = mapped_column(
        String(16), default="voice", nullable=False
    )  # voice | text | api
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)

    current_agent: Mapped[str] = mapped_column(
        String(64), default="main_agent", nullable=False
    )
    previous_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)

    cart_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id"), nullable=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True
    )

    current_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_summary: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )
    # Cached results of recent tool calls, keyed by tool name — lets a
    # specialist avoid re-fetching what the main agent already looked up.
    relevant_tool_results: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    handoffs: Mapped[list[Handoff]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )


class Handoff(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "handoffs"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    to_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    conversation_summary: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )
    shared_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    return_to_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)

    session: Mapped[AgentSession] = relationship(back_populates="handoffs")
