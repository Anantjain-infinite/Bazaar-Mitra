from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.cart import (
    AddCartItemRequest,
    CartItemOut,
    CartOut,
    CreateCartRequest,
    UpdateCartItemRequest,
)
from app.services import cart_service, catalog_service
from app.services.cart_service import (
    CartError,
    CartNotOpenError,
    ProductNotInMerchantError,
)

router = APIRouter(prefix="/api/v1/carts", tags=["carts"])


def _error_to_http(exc: CartError) -> HTTPException:
    if isinstance(exc, CartNotOpenError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProductNotInMerchantError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("", response_model=CartOut, status_code=201)
async def create_cart(
    body: CreateCartRequest, db: AsyncSession = Depends(get_db)
) -> CartOut:
    merchant = await catalog_service.get_merchant(db, body.merchant_id)
    if merchant is None:
        raise HTTPException(
            status_code=404, detail=f"No merchant found with id {body.merchant_id}"
        )
    cart = await cart_service.create_cart(db, body.merchant_id, body.buyer_id)
    await db.commit()
    cart = await cart_service.get_cart(db, cart.id)
    return CartOut.model_validate(cart)


@router.get("/{cart_id}", response_model=CartOut)
async def get_cart(cart_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> CartOut:
    cart = await cart_service.get_cart(db, cart_id)
    if cart is None:
        raise HTTPException(status_code=404, detail=f"No cart found with id {cart_id}")
    return CartOut.model_validate(cart)


@router.post("/{cart_id}/items", response_model=CartItemOut, status_code=201)
async def add_cart_item(
    cart_id: uuid.UUID, body: AddCartItemRequest, db: AsyncSession = Depends(get_db)
) -> CartItemOut:
    cart = await cart_service.get_cart(db, cart_id)
    if cart is None:
        raise HTTPException(status_code=404, detail=f"No cart found with id {cart_id}")
    try:
        item = await cart_service.add_item(db, cart, body.product_id, body.quantity)
    except CartError as exc:
        await db.rollback()
        raise _error_to_http(exc) from exc
    await db.commit()
    await db.refresh(item)
    return CartItemOut.model_validate(item)


@router.patch("/{cart_id}/items/{item_id}", response_model=CartItemOut)
async def update_cart_item(
    cart_id: uuid.UUID,
    item_id: uuid.UUID,
    body: UpdateCartItemRequest,
    db: AsyncSession = Depends(get_db),
) -> CartItemOut:
    cart = await cart_service.get_cart(db, cart_id)
    if cart is None:
        raise HTTPException(status_code=404, detail=f"No cart found with id {cart_id}")
    try:
        item = await cart_service.update_item_quantity(db, cart, item_id, body.quantity)
    except CartError as exc:
        await db.rollback()
        raise _error_to_http(exc) from exc
    await db.commit()
    await db.refresh(item)
    return CartItemOut.model_validate(item)


@router.delete("/{cart_id}/items/{item_id}", status_code=204)
async def delete_cart_item(
    cart_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    cart = await cart_service.get_cart(db, cart_id)
    if cart is None:
        raise HTTPException(status_code=404, detail=f"No cart found with id {cart_id}")
    try:
        await cart_service.remove_item(db, cart, item_id)
    except CartError as exc:
        await db.rollback()
        raise _error_to_http(exc) from exc
    await db.commit()
