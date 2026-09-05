from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin
from app.db.models.enums import CampaignStatus


class Campaign(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_agent: Mapped[str] = mapped_column(
        String(64), default="growth_agent", nullable=False
    )
    campaign_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # e.g. "cross_sell_offer"
    audience_definition: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    offer: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        String(24), default=CampaignStatus.DRAFT, nullable=False
    )

    # Merchant approval gate — a campaign can never move past PENDING_APPROVAL
    # without a human row here. Enforced in growth_service (Phase 8), not the LLM.
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    events: Mapped[list[CampaignEvent]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "campaign_events"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=True
    )

    targeted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opened: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clicked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revenue_generated: Mapped[float] = mapped_column(
        Numeric(12, 2), default=0, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="events")


class AgentRecommendation(UUIDPKMixin, TimestampMixin, Base):
    """Every upsell/cross-sell suggestion an agent surfaced to a buyer,
    whether or not it was accepted — this is what powers both the
    recommendation_service's ranking and the growth dashboard's
    'AI-assisted revenue' number.
    """

    __tablename__ = "agent_recommendations"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cart_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id"), nullable=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    source_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=True
    )

    recommendation_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # upsell | cross_sell | ...
    rationale: Mapped[str] = mapped_column(String(500), nullable=False)
    source_signal: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # e.g. "co_purchase_rate"
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0, nullable=False)

    accepted: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )  # NULL = not yet responded to
    converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revenue_impact: Mapped[float] = mapped_column(
        Numeric(12, 2), default=0, nullable=False
    )
