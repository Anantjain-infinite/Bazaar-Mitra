from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.merchant import MerchantSummary


class RelatedProductRef(BaseModel):
    """A related product reference as embedded in an agent-facing product —
    intentionally minimal (id/name/price/currency/available) so an agent can
    decide whether to fetch full detail rather than being handed everything.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price: float
    currency: str
    available: bool
    relationship_type: str
    reason: str | None = None


class AgentProduct(BaseModel):
    """Agent-readable product shape — matches the contract documented at
    GET /api/v1/agent/catalog. Every field here is read directly from the
    database at request time; nothing is cached or inferred, so an agent
    can never be handed a stale price or fabricated stock number.
    """

    id: uuid.UUID
    sku: str
    name: str
    description: str | None
    price: float
    currency: str
    stock: int
    category: str
    attributes: dict = Field(default_factory=dict)
    available: bool
    # e.g. ["max_quantity_per_order:5"] — sourced from product.metadata_,
    # never invented. Empty list means no constraints are configured.
    purchase_constraints: list[str] = Field(default_factory=list)
    merchant: MerchantSummary
    related_products: list[RelatedProductRef] = Field(default_factory=list)
    upsell_products: list[RelatedProductRef] = Field(default_factory=list)
    cross_sell_products: list[RelatedProductRef] = Field(default_factory=list)


class AgentCatalogResponse(BaseModel):
    as_of: str
    count: int
    products: list[AgentProduct]


class NaturalLanguageSearchRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        examples=["wireless mouse under ₹1000 in stock"],
    )
    merchant_id: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ParsedSearchFilters(BaseModel):
    """What the query was interpreted as — returned alongside results so
    the caller (human or agent) can see exactly how the free-text was
    understood, rather than trusting a black box.
    """

    keywords: list[str]
    max_price: float | None = None
    min_price: float | None = None
    in_stock_only: bool = False
    category: str | None = None


class NaturalLanguageSearchResponse(BaseModel):
    as_of: str
    interpreted_as: ParsedSearchFilters
    count: int
    products: list[AgentProduct]
