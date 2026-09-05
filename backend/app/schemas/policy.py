from __future__ import annotations

from pydantic import BaseModel, Field


class PolicyCheckResult(BaseModel):
    """The deterministic, backend-computed answer to "is this transaction
    allowed" — see app.services.policy_service. An LLM may explain this
    result to the user, but must never override it: every caller in this
    codebase that reaches a financial action re-checks this itself rather
    than trusting a value that passed through model output.
    """

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    requires_confirmation: bool
    amount: float
    currency: str
    max_transaction_amount: float
    max_daily_amount: float
    daily_spend_before: float
    daily_spend_after: float
