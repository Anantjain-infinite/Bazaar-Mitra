from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import OrderStatus
from app.db.session import get_db
from app.schemas.order import (
    ConfirmOrderResponse,
    CreateOrderRequest,
    CreateOrderResponse,
    OrderOut,
)
from app.schemas.payment import OrderWithPaymentsOut
from app.services import order_service
from app.services.order_service import (
    EmptyCartError,
    OutOfStockError,
    PriceOrStockChangedError,
)
from app.services.policy_service import validate_transaction

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.get("", response_model=list[OrderWithPaymentsOut])
async def list_orders(
    merchant_id: uuid.UUID | None = Query(default=None),
    buyer_id: uuid.UUID | None = Query(default=None),
    status: OrderStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[OrderWithPaymentsOut]:
    orders = await order_service.list_orders(
        db,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [OrderWithPaymentsOut.model_validate(o) for o in orders]


@router.post("", response_model=CreateOrderResponse, status_code=201)
async def create_order(
    body: CreateOrderRequest, db: AsyncSession = Depends(get_db)
) -> CreateOrderResponse:
    try:
        order = await order_service.create_order_from_cart(
            db, body.cart_id, acknowledge_price_change=body.acknowledge_price_change
        )
    except EmptyCartError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PriceOrStockChangedError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "price_changed",
                "message": "One or more prices changed since being added to the cart. "
                "Show the buyer the new total and retry with acknowledge_price_change=true "
                "only after they explicitly re-confirm.",
                "issues": [issue.model_dump(mode="json") for issue in exc.issues],
            },
        ) from exc
    except OutOfStockError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "out_of_stock",
                "message": "One or more items are no longer available in the requested quantity. "
                "This cannot be overridden — adjust the cart and try again.",
                "issues": [issue.model_dump(mode="json") for issue in exc.issues],
            },
        ) from exc

    await db.commit()
    order = await order_service.get_order(db, order.id)
    policy = await validate_transaction(
        db,
        merchant_id=order.merchant_id,
        buyer_id=order.buyer_id,
        amount=order.total,
        currency=order.currency,
    )
    return CreateOrderResponse(order=OrderOut.model_validate(order), policy=policy)


@router.get("/{order_id}", response_model=OrderWithPaymentsOut)
async def get_order(
    order_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> OrderWithPaymentsOut:
    order = await order_service.get_order(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=404, detail=f"No order found with id {order_id}"
        )
    return OrderWithPaymentsOut.model_validate(order)


@router.post("/{order_id}/confirm", response_model=ConfirmOrderResponse)
async def confirm_order(
    order_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ConfirmOrderResponse:
    """The explicit-confirmation gate. Requires the order to exist and be
    pending confirmation; runs the deterministic policy engine one more
    time and only transitions the order to CONFIRMED if it passes. If the
    policy check fails, the order is returned unchanged (still
    PENDING_CONFIRMATION) along with the reasons — never silently
    confirmed.
    """
    try:
        order, policy = await order_service.confirm_order(db, order_id)
    except OutOfStockError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "out_of_stock",
                "message": "One or more items are no longer available. This order cannot be confirmed as-is.",
                "issues": [issue.model_dump(mode="json") for issue in exc.issues],
            },
        ) from exc

    await db.commit()
    order = await order_service.get_order(db, order.id)
    return ConfirmOrderResponse(order=OrderOut.model_validate(order), policy=policy)
