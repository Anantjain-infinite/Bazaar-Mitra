from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPKMixin
from app.db.models.enums import RelationshipType


class Product(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "products"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    category: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Free-form agent-facing attributes, e.g. {"color": "black", "wireless": true}
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    merchant: Mapped[Merchant] = relationship(back_populates="products")  # noqa: F821

    __table_args__ = (
        {
            "comment": "Merchant-owned catalog items, the source of truth for price/stock."
        },
    )

    @property
    def available(self) -> bool:
        return self.active and self.stock_quantity > 0

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.sku} {self.name} ₹{self.price}>"


class ProductRelationship(UUIDPKMixin, TimestampMixin, Base):
    """Directed relationship used to power upsell/cross-sell/bundle recommendations."""

    __tablename__ = "product_relationships"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    related_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type: Mapped[RelationshipType] = mapped_column(
        String(32), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    product: Mapped[Product] = relationship(foreign_keys=[product_id])
    related_product: Mapped[Product] = relationship(foreign_keys=[related_product_id])
