"""AgentContext — the structured, DB-backed session context every agent
and tool in this package operates on (spec section 13).

The critical design constraint carried over from the existing voice
agent's `CallState` (see `backend/src/agent.py`): a specialist handoff
creates a brand-new agent instance with no memory of anything stored
only on `self`, so this context is always loaded from / saved back to
the `agent_sessions` table (via `session_service`), never held only in
a Python object across a handoff boundary.

Typical usage inside a tool function:

    ctx = await load_context(db, session_id)
    ... do the tool's work, possibly mutating ctx.cart_id etc. ...
    await save_context(db, ctx)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentSession
from app.db.models.enums import ActorType
from app.schemas.context import ActorContext
from app.services import session_service


@dataclass
class AgentContext:
    session_id: uuid.UUID
    merchant_id: uuid.UUID | None = None
    buyer_id: uuid.UUID | None = None
    language: str = "en"
    cart_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    payment_id: uuid.UUID | None = None
    current_topic: str | None = None
    conversation_summary: str | None = None
    relevant_tool_results: dict = field(default_factory=dict)
    previous_agent: str | None = None
    current_agent: str = "main_agent"

    @classmethod
    def from_session(cls, session: AgentSession) -> AgentContext:
        return cls(
            session_id=session.id,
            merchant_id=session.merchant_id,
            buyer_id=session.buyer_id,
            language=session.language,
            cart_id=session.cart_id,
            order_id=session.order_id,
            payment_id=session.payment_id,
            current_topic=session.current_topic,
            conversation_summary=session.conversation_summary,
            relevant_tool_results=dict(session.relevant_tool_results or {}),
            previous_agent=session.previous_agent,
            current_agent=session.current_agent,
        )

    def to_actor(self, agent_name: str | None = None) -> ActorContext:
        """Build the ActorContext a tool should pass to services/audit_service
        for this context — actor_type is derived from the current (or
        explicitly given) agent name.
        """
        name = agent_name or self.current_agent
        actor_type_map = {
            "main_agent": ActorType.MAIN_AGENT,
            "payments_agent": ActorType.PAYMENTS_AGENT,
            "returns_agent": ActorType.RETURNS_AGENT,
            "order_support_agent": ActorType.ORDER_SUPPORT_AGENT,
            "growth_agent": ActorType.GROWTH_AGENT,
            "buyer_agent": ActorType.BUYER_AGENT,
        }
        return ActorContext(
            actor_type=actor_type_map.get(name, ActorType.SYSTEM),
            actor_id=str(self.buyer_id) if self.buyer_id else None,
            agent_name=name,
            session_id=self.session_id,
        )


async def load_context(db: AsyncSession, session_id: uuid.UUID) -> AgentContext:
    session = await session_service.get_session(db, session_id)
    if session is None:
        raise ValueError(f"No agent session found with id {session_id}")
    return AgentContext.from_session(session)


async def save_context(db: AsyncSession, ctx: AgentContext) -> None:
    """Persist the context back to `agent_sessions` — and COMMIT, not
    just flush. This is the layer that has to guarantee "a brand-new
    agent instance can reload this," so a flush that a later rollback
    (or a request ending without an explicit commit) could silently
    discard would defeat the entire point of AgentContext. Several tool
    functions call this as their last state-changing step, sometimes
    after their own earlier `db.commit()` for the primary action (e.g.
    checkout commits the Order, then save_context needs to independently
    commit the session's new order_id) — an unconditional commit here is
    what actually makes both durable.
    """
    await session_service.update_session(
        db,
        ctx.session_id,
        merchant_id=ctx.merchant_id,
        buyer_id=ctx.buyer_id,
        language=ctx.language,
        cart_id=ctx.cart_id,
        order_id=ctx.order_id,
        payment_id=ctx.payment_id,
        current_topic=ctx.current_topic,
        conversation_summary=ctx.conversation_summary,
        relevant_tool_results=ctx.relevant_tool_results,
        current_agent=ctx.current_agent,
        previous_agent=ctx.previous_agent,
    )
    await db.commit()
