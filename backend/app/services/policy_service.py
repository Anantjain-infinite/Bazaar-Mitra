"""Policy engine — deterministic, backend-enforced transaction limits.

This is the one place in the codebase allowed to decide whether a
financial action is allowed. Nothing about the limits themselves lives
in a prompt; an agent can call this and explain the result, but every
consequential action (order confirmation in this phase, payment creation
in Phase 3) re-runs this check itself rather than trusting a value that
passed through model output.

Resolution order for the effective limit on a given (merchant, buyer):
  1. A buyer-specific transaction_policies row for this merchant, if one exists.
  2. The merchant's default transaction_policies row (buyer_id IS NULL).
  3. The app-wide Settings defaults (MAX_TRANSACTION_AMOUNT / DAILY_TRANSACTION_LIMIT),
     as a last-resort fallback so a merchant that was never given an explicit
     policy row doesn't silently allow unlimited spending.

On top of whichever policy row is resolved, the Buyer's own
`max_transaction_amount` / `max_daily_amount` (if set) are applied as an
additional, independent cap — the *lower* of the two always wins. This
lets an operator clamp a specific buyer (e.g. a cautious limit on an AI
buyer) without having to create a policy row per merchant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Buyer, Order, Payment, TransactionPolicy
from app.db.models.enums import PaymentStatus
from app.schemas.policy import PolicyCheckResult

settings = get_settings()


async def _resolve_limits(
    db: AsyncSession, merchant_id: uuid.UUID, buyer_id: uuid.UUID
) -> tuple[Decimal, Decimal]:
    """Return (max_transaction_amount, max_daily_amount) after applying
    the merchant-policy resolution order and the buyer-level cap.
    """
    stmt = select(TransactionPolicy).where(
        TransactionPolicy.merchant_id == merchant_id,
        TransactionPolicy.enabled.is_(True),
        TransactionPolicy.buyer_id == buyer_id,
    )
    buyer_policy = (await db.execute(stmt)).scalar_one_or_none()

    policy = buyer_policy
    if policy is None:
        stmt = select(TransactionPolicy).where(
            TransactionPolicy.merchant_id == merchant_id,
            TransactionPolicy.enabled.is_(True),
            TransactionPolicy.buyer_id.is_(None),
        )
        policy = (await db.execute(stmt)).scalar_one_or_none()

    if policy is not None:
        max_txn = Decimal(str(policy.max_transaction_amount))
        max_daily = Decimal(str(policy.max_daily_amount))
    else:
        max_txn = Decimal(str(settings.max_transaction_amount))
        max_daily = Decimal(str(settings.daily_transaction_limit))

    buyer = await db.get(Buyer, buyer_id)
    if buyer is not None:
        if buyer.max_transaction_amount is not None:
            max_txn = min(max_txn, Decimal(str(buyer.max_transaction_amount)))
        if buyer.max_daily_amount is not None:
            max_daily = min(max_daily, Decimal(str(buyer.max_daily_amount)))

    return max_txn, max_daily


async def _daily_spend(
    db: AsyncSession, merchant_id: uuid.UUID, buyer_id: uuid.UUID
) -> Decimal:
    """Sum of CAPTURED payment amounts for this buyer at this merchant
    since the start of today (UTC).
    """
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = (
        select(func.coalesce(func.sum(Payment.amount), 0))
        .join(Order, Payment.order_id == Order.id)
        .where(
            Order.merchant_id == merchant_id,
            Order.buyer_id == buyer_id,
            Payment.status == PaymentStatus.CAPTURED,
            Payment.created_at >= start_of_day,
        )
    )
    result = await db.execute(stmt)
    total = result.scalar_one()
    return Decimal(str(total))


async def validate_transaction(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    buyer_id: uuid.UUID,
    amount: Decimal | float,
    currency: str = "INR",
) -> PolicyCheckResult:
    """The single entry point for "is this transaction allowed". Every
    check here is a plain comparison against real database values — no
    guessing, no LLM involvement.
    """
    amount = Decimal(str(amount))
    reasons: list[str] = []

    max_txn, max_daily = await _resolve_limits(db, merchant_id, buyer_id)
    daily_spend_before = await _daily_spend(db, merchant_id, buyer_id)
    daily_spend_after = daily_spend_before + amount

    if amount <= 0:
        reasons.append("Transaction amount must be positive")
    if amount > max_txn:
        reasons.append(
            f"Transaction of {currency} {amount} exceeds the per-transaction limit of {currency} {max_txn}"
        )
    if daily_spend_after > max_daily:
        reasons.append(
            f"This transaction would bring today's total to {currency} {daily_spend_after}, "
            f"exceeding the daily limit of {currency} {max_daily}"
        )
    if currency != settings.default_currency and currency != "INR":
        # Currently the whole platform only operates in INR — flagged
        # explicitly rather than silently accepted.
        reasons.append(f"Unsupported currency: {currency}")

    return PolicyCheckResult(
        allowed=len(reasons) == 0,
        reasons=reasons,
        requires_confirmation=True,
        amount=float(amount),
        currency=currency,
        max_transaction_amount=float(max_txn),
        max_daily_amount=float(max_daily),
        daily_spend_before=float(daily_spend_before),
        daily_spend_after=float(daily_spend_after),
    )
