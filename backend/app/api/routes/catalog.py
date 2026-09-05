from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.catalog import (
    AgentCatalogResponse,
    AgentProduct,
    NaturalLanguageSearchResponse,
)
from app.schemas.product import ProductOut
from app.services import catalog_service

router = APIRouter(prefix="/api/v1/merchants/{merchant_id}", tags=["catalog"])


async def _require_merchant(merchant_id: uuid.UUID, db: AsyncSession):
    merchant = await catalog_service.get_merchant(db, merchant_id)
    if merchant is None:
        raise HTTPException(
            status_code=404, detail=f"No merchant found with id {merchant_id}"
        )
    return merchant


@router.get("/catalog", response_model=AgentCatalogResponse)
async def get_merchant_catalog(
    merchant_id: uuid.UUID,
    category: str | None = Query(default=None),
    in_stock_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> AgentCatalogResponse:
    """The agent-readable catalog for a single merchant — same product
    shape as the cross-merchant /api/v1/agent/catalog endpoint, scoped to
    one shop. Useful once an agent has already picked a merchant.
    """
    await _require_merchant(merchant_id, db)
    products = await catalog_service.get_agent_catalog(
        db,
        merchant_id=merchant_id,
        category=category,
        in_stock_only=in_stock_only,
        limit=limit,
    )
    return AgentCatalogResponse(
        as_of=catalog_service.today_str(), count=len(products), products=products
    )


@router.get("/products", response_model=list[ProductOut])
async def list_merchant_products(
    merchant_id: uuid.UUID,
    category: str | None = Query(default=None),
    in_stock_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ProductOut]:
    """Plain product listing for human-facing UI (the merchant dashboard's
    Products tab). For the richer agent shape with related/upsell/cross-sell
    products, use /catalog instead.
    """
    await _require_merchant(merchant_id, db)
    products = await catalog_service.list_products(
        db,
        merchant_id,
        category=category,
        in_stock_only=in_stock_only,
        limit=limit,
        offset=offset,
    )
    return [ProductOut.model_validate(p) for p in products]


@router.get("/products/{product_id}", response_model=AgentProduct)
async def get_merchant_product(
    merchant_id: uuid.UUID, product_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> AgentProduct:
    merchant = await _require_merchant(merchant_id, db)
    product = await catalog_service.get_product(db, merchant_id, product_id)
    if product is None:
        raise HTTPException(
            status_code=404,
            detail=f"No product {product_id} found for merchant {merchant_id}",
        )
    return await catalog_service.to_agent_product(db, product, merchant=merchant)


@router.get("/search", response_model=NaturalLanguageSearchResponse)
async def search_merchant_catalog(
    merchant_id: uuid.UUID,
    q: str = Query(
        ...,
        min_length=1,
        description='Natural-language query, e.g. "wireless mouse under 1000"',
    ),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> NaturalLanguageSearchResponse:
    await _require_merchant(merchant_id, db)
    filters, products = await catalog_service.natural_language_search(
        db, q, merchant_id=merchant_id, limit=limit
    )
    return NaturalLanguageSearchResponse(
        as_of=catalog_service.today_str(),
        interpreted_as=filters,
        count=len(products),
        products=products,
    )
