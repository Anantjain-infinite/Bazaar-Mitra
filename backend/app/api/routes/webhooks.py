from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import payment_service
from app.services.payment_service import InvalidWebhookSignatureError

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    """Razorpay webhook receiver.

    Reads the RAW request body — not `await request.json()` — because
    signature verification is computed over the exact bytes Razorpay
    sent. Re-serializing parsed JSON before verifying is a documented
    footgun (whitespace/key-order differences break the HMAC), so the
    raw body is read first and parsed only after the signature checks out.

    Configure this URL in the Razorpay dashboard under Settings ->
    Webhooks, subscribed to payment.captured, payment.failed, and
    order.paid, with a webhook secret matching RAZORPAY_WEBHOOK_SECRET.
    For local development, expose this endpoint with a tunnel (e.g. ngrok)
    since Razorpay's servers need to reach it directly.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    try:
        result = await payment_service.handle_webhook_event(
            db, raw_body=raw_body, signature=signature
        )
    except InvalidWebhookSignatureError as exc:
        await db.rollback()
        # 400, not 401/403 — Razorpay's retry behavior treats any non-2xx
        # as "retry later", which is exactly right for a bad signature:
        # don't process it, but don't leak which failure mode either.
        raise HTTPException(
            status_code=400, detail="Invalid webhook signature"
        ) from exc

    await db.commit()
    return result
