"""Catalog service — the single place that knows how to read merchants,
products, and product relationships out of the database.

Everything an agent (voice, AI Buyer, or any future integration) is ever
told about price, stock, or availability flows through this module and
therefore through a live DB query. There is no caching layer here on
purpose — see AGENTS.md/spec section 53 on why caching payment-adjacent
reads is unsafe; catalog reads are cheap enough to just always be fresh.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Merchant, Product, ProductRelationship
from app.schemas.catalog import AgentProduct, ParsedSearchFilters, RelatedProductRef
from app.schemas.merchant import MerchantSummary

# --- Merchants -----------------------------------------------------------


async def list_merchants(
    db: AsyncSession,
    *,
    city: str | None = None,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Merchant]:
    stmt = select(Merchant)
    if active_only:
        stmt = stmt.where(Merchant.active.is_(True))
    if city:
        stmt = stmt.where(Merchant.city.ilike(city))
    stmt = stmt.order_by(Merchant.business_name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_merchant(db: AsyncSession, merchant_id: uuid.UUID) -> Merchant | None:
    return await db.get(Merchant, merchant_id)


# --- Products --------------------------------------------------------------


async def list_products(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    *,
    category: str | None = None,
    active_only: bool = True,
    in_stock_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Product]:
    stmt = select(Product).where(Product.merchant_id == merchant_id)
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    if in_stock_only:
        stmt = stmt.where(Product.stock_quantity > 0)
    if category:
        stmt = stmt.where(Product.category.ilike(category))
    stmt = stmt.order_by(Product.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_product(
    db: AsyncSession, merchant_id: uuid.UUID, product_id: uuid.UUID
) -> Product | None:
    stmt = select(Product).where(
        Product.id == product_id, Product.merchant_id == merchant_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_by_id(db: AsyncSession, product_id: uuid.UUID) -> Product | None:
    return await db.get(Product, product_id)


# --- Relationships / agent-readable shaping --------------------------------

_RELATIONSHIP_REASON = {
    "FREQUENTLY_BOUGHT_TOGETHER": "Frequently bought together",
    "UPSELL": "A higher-value alternative buyers often prefer",
    "CROSS_SELL": "Commonly purchased alongside this item",
    "ALTERNATIVE": "A comparable alternative",
    "BUNDLE": "Available as a bundle with this item",
    "ACCESSORY": "A commonly paired accessory",
}


async def _related_refs(
    db: AsyncSession, product_id: uuid.UUID, relationship_type: str
) -> list[RelatedProductRef]:
    stmt = (
        select(ProductRelationship)
        .where(
            ProductRelationship.product_id == product_id,
            ProductRelationship.relationship_type == relationship_type,
        )
        .options(selectinload(ProductRelationship.related_product))
        .order_by(ProductRelationship.priority.desc())
    )
    result = await db.execute(stmt)
    refs = []
    for rel in result.scalars().all():
        rp = rel.related_product
        if rp is None:
            continue
        refs.append(
            RelatedProductRef(
                id=rp.id,
                name=rp.name,
                price=float(rp.price),
                currency=rp.currency,
                available=rp.available,
                relationship_type=rel.relationship_type,
                reason=_RELATIONSHIP_REASON.get(rel.relationship_type),
            )
        )
    return refs


async def to_agent_product(
    db: AsyncSession, product: Product, merchant: Merchant | None = None
) -> AgentProduct:
    """Build the full agent-readable shape for one product, including its
    live related/upsell/cross-sell references. Always issues fresh queries
    — see module docstring on why nothing here is cached.
    """
    if merchant is None:
        merchant = await db.get(Merchant, product.merchant_id)

    related, upsell, cross_sell = [], [], []
    for rel_type, bucket in (
        ("FREQUENTLY_BOUGHT_TOGETHER", related),
        ("ALTERNATIVE", related),
        ("BUNDLE", related),
        ("ACCESSORY", related),
        ("UPSELL", upsell),
        ("CROSS_SELL", cross_sell),
    ):
        bucket.extend(await _related_refs(db, product.id, rel_type))

    constraints: list[str] = []
    max_qty = (product.metadata_ or {}).get("max_quantity_per_order")
    if max_qty:
        constraints.append(f"max_quantity_per_order:{max_qty}")

    return AgentProduct(
        id=product.id,
        sku=product.sku,
        name=product.name,
        description=product.description,
        price=float(product.price),
        currency=product.currency,
        stock=product.stock_quantity,
        category=product.category,
        attributes=product.metadata_ or {},
        available=product.available,
        purchase_constraints=constraints,
        merchant=MerchantSummary.model_validate(merchant),
        related_products=related,
        upsell_products=upsell,
        cross_sell_products=cross_sell,
    )


async def get_agent_catalog(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID | None = None,
    category: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    in_stock_only: bool = False,
    limit: int = 50,
) -> list[AgentProduct]:
    """The cross-merchant, agent-oriented catalog view backing
    GET /api/v1/agent/catalog. Every filter is applied server-side against
    live rows — an agent can narrow the request, but can never get back a
    product whose price/stock isn't the actual current DB value.
    """
    stmt = (
        select(Product)
        .where(Product.active.is_(True))
        .options(selectinload(Product.merchant))
    )
    if merchant_id:
        stmt = stmt.where(Product.merchant_id == merchant_id)
    if category:
        stmt = stmt.where(Product.category.ilike(category))
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
    if in_stock_only:
        stmt = stmt.where(Product.stock_quantity > 0)
    stmt = stmt.join(Merchant, Product.merchant_id == Merchant.id).where(
        Merchant.active.is_(True)
    )
    stmt = stmt.order_by(Product.price).limit(limit)

    result = await db.execute(stmt)
    products = list(result.scalars().unique().all())
    return [await to_agent_product(db, p, merchant=p.merchant) for p in products]


# --- Natural-language search -----------------------------------------------

# Deterministic, regex-based NL -> structured-filter parsing. This is
# intentionally NOT an LLM call: search is on the read path that feeds
# price/stock decisions, and a regex parser is auditable and reproducible
# in a way a model call isn't. An agent (voice or AI Buyer) is free to do
# its own smarter query rewriting upstream and call get_agent_catalog
# directly with explicit filters instead.

_MAX_PRICE_PATTERNS = [
    r"(?:under|below|less\s*than|within|max(?:imum)?)\s*(?:₹|rs\.?|inr)?\s*([\d,]+)",
    r"(?:₹|rs\.?|inr)?\s*([\d,]+)\s*(?:ke\s*andar|se\s*kam|tak|k\s*andar)",
]
_MIN_PRICE_PATTERNS = [
    r"(?:above|over|more\s*than|min(?:imum)?)\s*(?:₹|rs\.?|inr)?\s*([\d,]+)",
    r"(?:₹|rs\.?|inr)?\s*([\d,]+)\s*(?:se\s*zyada|se\s*upar)",
]
_STOCK_PATTERNS = [
    r"in\s*stock",
    r"available",
    r"stock\s*mein",
    r"available\s*hai",
]

_STOPWORDS = {
    "a",
    "an",
    "the",
    "for",
    "me",
    "find",
    "i",
    "need",
    "want",
    "please",
    "show",
    "get",
    "buy",
    "some",
    "any",
    "is",
    "are",
    "of",
    "in",
    "that",
    "which",
    "under",
    "below",
    "less",
    "than",
    "within",
    "max",
    "maximum",
    "above",
    "over",
    "more",
    "min",
    "minimum",
    "stock",
    "available",
    "chahiye",
    "ek",
    "ka",
    "ki",
    "hai",
    "mujhe",
    "mera",
    "meri",
    "ke",
    "andar",
    "se",
    "kam",
    "tak",
    "rupaye",
    "rupaya",
    "rs",
    "inr",
    "aur",
    "ko",
    "और",
    "मुझे",
    "चाहिए",
}


def parse_natural_language_query(query: str) -> ParsedSearchFilters:
    text = query.strip().lower()
    max_price: float | None = None
    min_price: float | None = None
    in_stock_only = False

    for pattern in _MAX_PRICE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            max_price = float(m.group(1).replace(",", ""))
            text = text[: m.start()] + text[m.end() :]
            break

    for pattern in _MIN_PRICE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            min_price = float(m.group(1).replace(",", ""))
            text = text[: m.start()] + text[m.end() :]
            break

    for pattern in _STOCK_PATTERNS:
        if re.search(pattern, text):
            in_stock_only = True
            text = re.sub(pattern, " ", text)

    # Strip currency symbols/punctuation, then tokenize remaining free text.
    text = re.sub(r"[₹,.!?]", " ", text)
    tokens = [t for t in re.split(r"\s+", text) if t]
    keywords = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]

    return ParsedSearchFilters(
        keywords=keywords,
        max_price=max_price,
        min_price=min_price,
        in_stock_only=in_stock_only,
        category=None,
    )


async def natural_language_search(
    db: AsyncSession,
    query: str,
    *,
    merchant_id: uuid.UUID | None = None,
    limit: int = 20,
    include_out_of_stock: bool = False,
) -> tuple[ParsedSearchFilters, list[AgentProduct]]:
    """`include_out_of_stock=True` is for callers that want to see and
    explain unavailable options (e.g. the AI Buyer's cross-merchant
    comparison, which wants to say "Merchant C was cheaper but out of
    stock") even when the query itself says "in stock" — the parsed
    `filters.in_stock_only` is still reported accurately in the
    response either way; this only controls whether it's applied as a
    SQL filter that would otherwise remove those rows entirely.
    """
    filters = parse_natural_language_query(query)

    stmt = (
        select(Product)
        .where(Product.active.is_(True))
        .options(selectinload(Product.merchant))
    )
    stmt = stmt.join(Merchant, Product.merchant_id == Merchant.id).where(
        Merchant.active.is_(True)
    )

    if merchant_id:
        stmt = stmt.where(Product.merchant_id == merchant_id)
    if filters.max_price is not None:
        stmt = stmt.where(Product.price <= filters.max_price)
    if filters.min_price is not None:
        stmt = stmt.where(Product.price >= filters.min_price)
    if filters.in_stock_only and not include_out_of_stock:
        stmt = stmt.where(Product.stock_quantity > 0)
    if filters.keywords:
        keyword_clauses = [
            or_(
                Product.name.ilike(f"%{kw}%"),
                Product.description.ilike(f"%{kw}%"),
                Product.category.ilike(f"%{kw}%"),
            )
            for kw in filters.keywords
        ]
        # AND across keywords (every keyword must match something) keeps
        # results precise for short queries like "wireless mouse".
        for clause in keyword_clauses:
            stmt = stmt.where(clause)

    stmt = stmt.order_by(Product.price).limit(limit)
    result = await db.execute(stmt)
    products = list(result.scalars().unique().all())
    agent_products = [
        await to_agent_product(db, p, merchant=p.merchant) for p in products
    ]
    return filters, agent_products


def today_str() -> str:
    return datetime.now(UTC).date().isoformat()
