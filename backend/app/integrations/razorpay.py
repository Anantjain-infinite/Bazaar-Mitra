"""Razorpay integration.

Every function here corresponds to a documented Razorpay API call or
signature-verification routine:

- `create_order` uses the official `razorpay` Python SDK's Orders API
  (https://razorpay.com/docs/payments/orders/apis/) rather than
  hand-rolled HTTP, so auth headers, base URL, and request shape are the
  SDK's, not guessed.
- `verify_payment_signature` implements the exact documented formula
  (https://razorpay.com/docs/payments/third-party-validation/standard-integration/):
      generated_signature = hmac_sha256(order_id + "|" + razorpay_payment_id, key_secret)
  compared to the `razorpay_signature` returned by Checkout. Implemented
  directly with stdlib `hmac`/`hashlib` rather than through the SDK's
  exception-raising utility class, so the service layer gets a plain
  boolean and this is trivially unit-testable without any network access
  — verification is pure HMAC computation against a secret we already
  hold, not a call to Razorpay's servers.
- `verify_webhook_signature` implements the documented webhook formula
  (https://razorpay.com/docs/webhooks/): HMAC-SHA256 of the RAW request
  body (not re-serialized JSON — that's a documented pitfall) using the
  webhook secret, compared against the `X-Razorpay-Signature` header.

IMPORTANT — environment limitation: this was built in a sandbox that
cannot reach api.razorpay.com, so `create_order`'s actual network call
has never been exercised against the real API in this codebase. Every
code path here is covered by tests that mock the SDK client, verifying
this module's plumbing (what request it builds, how it parses the
response, and that both signature formulas are implemented exactly as
documented — the latter fully verifiable with zero network access).
Exercising `create_order` against real Razorpay test-mode credentials is
the one thing that still needs to happen in a normal dev environment.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any

import razorpay

from app.config import get_settings

settings = get_settings()


class RazorpayNotConfiguredError(Exception):
    """Raised when RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET aren't set — fails
    loudly rather than silently trying to call Razorpay with empty creds.
    """


@lru_cache
def get_client() -> razorpay.Client:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise RazorpayNotConfiguredError(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set. "
            "Copy backend/.env.example to .env.local and fill in your Razorpay test-mode keys."
        )
    return razorpay.Client(
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
    )


def rupees_to_paise(amount_rupees: Decimal) -> int:
    """Razorpay amounts are always in the smallest currency subunit —
    paise for INR, e.g. ₹222.25 -> 22225. See
    https://razorpay.com/docs/api/orders/#create-an-order
    """
    return int((amount_rupees * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def create_order(
    *,
    amount_rupees: Decimal,
    currency: str,
    receipt: str,
    notes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a Razorpay Order — must happen before Checkout is opened,
    and the resulting order id ties the eventual payment to this exact
    amount so it can't be tampered with client-side.
    """
    client = get_client()
    payload = {
        "amount": rupees_to_paise(amount_rupees),
        "currency": currency,
        "receipt": receipt[:40],  # Razorpay caps receipt length at 40 chars
        "notes": notes or {},
    }
    return client.order.create(data=payload)


def verify_payment_signature(
    *, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str
) -> bool:
    if not settings.razorpay_key_secret:
        raise RazorpayNotConfiguredError("RAZORPAY_KEY_SECRET is not set.")
    message = f"{razorpay_order_id}|{razorpay_payment_id}".encode()
    expected = hmac.new(
        settings.razorpay_key_secret.encode(), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    if not settings.razorpay_webhook_secret:
        raise RazorpayNotConfiguredError("RAZORPAY_WEBHOOK_SECRET is not set.")
    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
