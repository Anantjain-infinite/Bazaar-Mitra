"""Cart service.

The one rule this module exists to enforce: `CartItem.unit_price` is the
price quoted to the buyer at the moment they added the item, and it is
never silently rewritten. Later phases (order creation) compare this
quoted price against the live product price and force a fresh
confirmation if it has drifted — see order_service.create_order_from_cart.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Cart, CartItem, Product
from app.db.models.enums import CartStatus


class CartError(Exception):
    """Base class for cart errors the API layer translates to HTTP responses."""


class CartNotOpenError(CartError):
    pass


class ProductNotInMerchantError(CartError):
    pass


async def create_cart(
    db: AsyncSession, merchant_id: uuid.UUID, buyer_id: uuid.UUID
) -> Cart:
    cart = Cart(
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        status=CartStatus.OPEN,
        currency="INR",
        subtotal=Decimal("0"),
        discount=Decimal("0"),
        total=Decimal("0"),
        items=[],  # explicit empty collection — a brand-new cart provably has none,
        # and setting it now avoids SQLAlchemy treating `.items` as "never loaded"
        # and trying to lazy-load it on next access after this flush (which fails
        # outside a query context in async mode). See test_phase2 comments.
    )
    db.add(cart)
    await db.flush()
    return cart


async def get_cart(db: AsyncSession, cart_id: uuid.UUID) -> Cart | None:
    stmt = select(Cart).where(Cart.id == cart_id).options(selectinload(Cart.items))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def recompute_cart_totals(cart: Cart) -> None:
    subtotal = sum((item.line_total for item in cart.items), Decimal("0"))
    cart.subtotal = subtotal
    cart.total = subtotal - cart.discount


async def add_item(
    db: AsyncSession, cart: Cart, product_id: uuid.UUID, quantity: int
) -> CartItem:
    if cart.status != CartStatus.OPEN:
        raise CartNotOpenError(
            f"Cart {cart.id} is {cart.status}, not open — items can't be added"
        )

    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=404, detail=f"No product found with id {product_id}"
        )
    if product.merchant_id != cart.merchant_id:
        raise ProductNotInMerchantError(
            f"Product {product_id} belongs to a different merchant than cart {cart.id}"
        )

    # If this product is already in the cart, increase quantity and
    # re-quote at the current price rather than creating a duplicate row.
    existing = next((i for i in cart.items if i.product_id == product_id), None)
    if existing:
        existing.quantity += quantity
        existing.unit_price = product.price
        existing.line_total = product.price * existing.quantity
        await db.flush()
        recompute_cart_totals(cart)
        await db.flush()
        return existing

    item = CartItem(
        cart_id=cart.id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.price,  # quoted NOW — see module docstring
        line_total=product.price * quantity,
    )
    db.add(item)
    cart.items.append(item)
    await db.flush()
    recompute_cart_totals(cart)
    await db.flush()
    return item


async def update_item_quantity(
    db: AsyncSession, cart: Cart, item_id: uuid.UUID, quantity: int
) -> CartItem:
    if cart.status != CartStatus.OPEN:
        raise CartNotOpenError(
            f"Cart {cart.id} is {cart.status}, not open — items can't be changed"
        )
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"No cart item {item_id} in cart {cart.id}"
        )
    item.quantity = quantity
    item.line_total = item.unit_price * quantity
    await db.flush()
    recompute_cart_totals(cart)
    await db.flush()
    return item


async def remove_item(db: AsyncSession, cart: Cart, item_id: uuid.UUID) -> None:
    if cart.status != CartStatus.OPEN:
        raise CartNotOpenError(
            f"Cart {cart.id} is {cart.status}, not open — items can't be removed"
        )
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"No cart item {item_id} in cart {cart.id}"
        )
    cart.items.remove(item)
    await db.delete(item)
    await db.flush()
    recompute_cart_totals(cart)
    await db.flush()
