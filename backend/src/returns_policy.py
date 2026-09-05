"""
returns_policy.py — Bazaar Mitra's return & refund policy (Day 9 specialist's domain
knowledge).

DATA SOURCE NOTE (for your README): there's no public API for a neighbourhood shop's
return policy — same honesty note as catalogue.py's hand-built product data. This is a
small hand-built policy, isolated in this one module so it's easy to point at a real
policy system later without touching the specialist agent itself.
"""

POLICY_LAST_UPDATED = "2026-08-13"

RETURN_WINDOW_DAYS = 7

# Categories that can never be returned once opened/used, regardless of the window.
NON_RETURNABLE_IF_OPENED = {"groceries"}

REFUND_METHOD = "Refund goes back to the original payment method, or store credit if paid in cash."
REFUND_TIMELINE = "3-5 business days after the shop receives the returned item"


def check_eligibility(category: str, days_since_purchase: int, opened_or_used: bool) -> dict:
    """Returns {"eligible": bool, "reason": str} using the policy above."""
    category = (category or "general").strip().lower()

    if days_since_purchase > RETURN_WINDOW_DAYS:
        return {
            "eligible": False,
            "reason": (
                f"Outside the {RETURN_WINDOW_DAYS}-day return window "
                f"({days_since_purchase} days since purchase)."
            ),
        }

    if opened_or_used and category in NON_RETURNABLE_IF_OPENED:
        return {
            "eligible": False,
            "reason": f"{category.capitalize()} items can't be returned once opened, per policy.",
        }

    return {
        "eligible": True,
        "reason": f"Within the {RETURN_WINDOW_DAYS}-day window and eligible under policy.",
    }