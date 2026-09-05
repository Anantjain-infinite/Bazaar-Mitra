from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    timestamp: datetime
    actor_type: str
    actor_id: str | None
    agent_name: str | None
    session_id: uuid.UUID | None
    merchant_id: uuid.UUID | None
    buyer_id: uuid.UUID | None
    event_type: str
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    amount: float | None
    currency: str | None
    explanation: str
    policy_result: str | None
    confirmation_state: str | None
    success: bool
    failure_reason: str | None
