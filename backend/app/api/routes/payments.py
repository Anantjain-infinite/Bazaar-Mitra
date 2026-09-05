from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.integrations.razorpay import RazorpayNotConfiguredError, rupees_to_paise
from app.schemas.payment import (
    CreatePaymentRequest,
    CreatePaymentResponse,
    PaymentOut,
    RetryPaymentRequest,
    VerifyPaymentRequest,
    VerifyPaymentResponse,
)
from app.services import order_service, payment_service
from app.services.payment_service import (
    InvalidSignatureError,
    OrderNotPayableError,
    PaymentGatewayUnavailableError,
    PolicyRejectedError,
)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])
settings = get_settings()


def _policy_rejected_http(exc: PolicyRejectedError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error": "policy_rejected",
            "message": "This transaction does not pass the merchant's policy checks right now.",
            "policy": exc.policy.model_dump(),
        },
    )


@router.post("/create", response_model=CreatePaymentResponse, status_code=201)
async def create_payment(
    body: CreatePaymentRequest, db: AsyncSession = Depends(get_db)
) -> CreatePaymentResponse:
    """Create a Razorpay order for a CONFIRMED order and return exactly
    what the frontend needs to open Razorpay Checkout. Never returns the
    Razorpay key secret — only the public key id.
    """
    try:
        payment = await payment_service.create_payment_attempt(
            db, body.order_id, idempotency_key=body.idempotency_key
        )
    except OrderNotPayableError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PolicyRejectedError as exc:
        await db.rollback()
        raise _policy_rejected_http(exc) from exc
    except PaymentGatewayUnavailableError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The payment service is temporarily unavailable. Your order has not been charged.",
        ) from exc
    except RazorpayNotConfiguredError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await db.commit()
    payment = await payment_service.get_payment(db, payment.id)
    return CreatePaymentResponse(
        payment=PaymentOut.model_validate(payment),
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_key_id=settings.razorpay_key_id,
        amount_paise=rupees_to_paise(payment.amount),
        currency=payment.currency,
    )


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(
    payment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> PaymentOut:
    payment = await payment_service.get_payment(db, payment_id)
    if payment is None:
        raise HTTPException(
            status_code=404, detail=f"No payment found with id {payment_id}"
        )
    return PaymentOut.model_validate(payment)


@router.post(
    "/{payment_id}/retry", response_model=CreatePaymentResponse, status_code=201
)
async def retry_payment(
    payment_id: uuid.UUID, body: RetryPaymentRequest, db: AsyncSession = Depends(get_db)
) -> CreatePaymentResponse:
    """Retry a failed payment — creates a fresh Razorpay order and a new
    Payment row (attempt_number + 1). The failed attempt is never
    overwritten; both remain in the order's payment history.
    """
    prior = await payment_service.get_payment(db, payment_id)
    if prior is None:
        raise HTTPException(
            status_code=404, detail=f"No payment found with id {payment_id}"
        )

    try:
        payment = await payment_service.create_payment_attempt(
            db, prior.order_id, idempotency_key=body.idempotency_key
        )
    except OrderNotPayableError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PolicyRejectedError as exc:
        await db.rollback()
        raise _policy_rejected_http(exc) from exc
    except PaymentGatewayUnavailableError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The payment service is temporarily unavailable. Your order has not been charged.",
        ) from exc

    await db.commit()
    payment = await payment_service.get_payment(db, payment.id)
    return CreatePaymentResponse(
        payment=PaymentOut.model_validate(payment),
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_key_id=settings.razorpay_key_id,
        amount_paise=rupees_to_paise(payment.amount),
        currency=payment.currency,
    )


@router.post("/verify", response_model=VerifyPaymentResponse)
async def verify_payment(
    body: VerifyPaymentRequest, db: AsyncSession = Depends(get_db)
) -> VerifyPaymentResponse:
    """Server-side signature verification of the Checkout response. This
    marks the Payment attempt AUTHORIZED but does NOT mark the order
    PAID — that only happens via verified webhook reconciliation
    (POST /api/v1/webhooks/razorpay). See payment_service module docstring.
    """
    try:
        payment = await payment_service.verify_payment(
            db,
            body.payment_id,
            razorpay_payment_id=body.razorpay_payment_id,
            razorpay_signature=body.razorpay_signature,
        )
        verified = True
    except InvalidSignatureError:
        await db.commit()  # persist the FAILED status written before the raise
        payment = await payment_service.get_payment(db, body.payment_id)
        verified = False
    else:
        await db.commit()
        payment = await payment_service.get_payment(db, payment.id)

    order = await order_service.get_order(db, payment.order_id)
    return VerifyPaymentResponse(
        payment=PaymentOut.model_validate(payment),
        order=order,
        verified=verified,
    )
