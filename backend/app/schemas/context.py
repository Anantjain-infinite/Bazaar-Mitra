"""ActorContext — a small, shared "who is doing this" descriptor threaded
through service functions so audit_service can record who/what initiated
a consequential action, without every service needing to know about
sessions, agents, or HTTP requests directly.

A bare HTTP caller (no agent involved) gets the default `ActorContext()`
— actor_type SYSTEM — which is honest: from the backend's point of view,
an unauthenticated-by-this-layer API call IS the system acting on the
caller's behalf. Once real auth/session context exists (see
AgentContext in app.agents.context), callers pass a populated
ActorContext instead.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.db.models.enums import ActorType


class ActorContext(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    actor_type: ActorType = ActorType.SYSTEM
    actor_id: str | None = None
    agent_name: str | None = None
    session_id: uuid.UUID | None = None
