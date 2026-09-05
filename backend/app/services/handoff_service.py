"""Handoff service — the specialist handoff protocol (spec sections 11-12).

Two things this module guarantees:

1. Shared context travels with the handoff. When `initiate_handoff` runs,
   it snapshots the session's current cart/order/payment ids and recent
   tool results into the `handoffs.shared_context` column, so the
   receiving specialist has everything the main agent already knew — the
   caller/user should never have to repeat themselves (spec section 11).

2. A handoff to an agent that isn't real (not yet implemented, disabled,
   or misspelled) fails cleanly and recoverably rather than leaving the
   session stuck. `initiate_handoff` raises `HandoffFailedError` for an
   unknown target agent; the caller (voice agent / API layer) is
   expected to catch this, explain to the user, and fall back to
   handling what it can itself — see spec section 12. This is also how
   the not-yet-built Growth agent's handoff currently behaves when
   requested from a buyer-facing context: growth_agent is real (Phase 8)
   but is merchant-facing, not buyer-facing, so a buyer-side handoff to
   it is deliberately rejected the same way an unknown agent would be.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Handoff
from app.services import session_service

# The full set of agent names this system knows about. Used to validate
# every handoff target — an unrecognized name always fails cleanly via
# HandoffFailedError rather than silently updating the session to an
# agent nothing implements.
KNOWN_AGENTS = {
    "main_agent",
    "payments_agent",
    "returns_agent",
    "order_support_agent",
    "growth_agent",
    "buyer_agent",
}

# Agents only reachable from a merchant-facing session, not a buyer-facing
# one — a buyer asking to be handed off to the growth agent is exactly
# the "specialist unavailable for this request" case spec section 12
# describes, even though growth_agent itself is real (Phase 8).
MERCHANT_ONLY_AGENTS = {"growth_agent"}


class HandoffFailedError(Exception):
    def __init__(self, to_agent: str, reason: str):
        self.to_agent = to_agent
        self.reason = reason
        super().__init__(f"Handoff to {to_agent} failed: {reason}")


async def initiate_handoff(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    to_agent: str,
    reason: str,
    conversation_summary: str | None = None,
) -> Handoff:
    session = await session_service.get_session(db, session_id)
    if session is None:
        raise ValueError(f"No agent session found with id {session_id}")

    from_agent = session.current_agent

    target_unavailable = to_agent not in KNOWN_AGENTS or (
        to_agent in MERCHANT_ONLY_AGENTS
        and session.merchant_id is None
        and to_agent != from_agent
    )

    shared_context = {
        "cart_id": str(session.cart_id) if session.cart_id else None,
        "order_id": str(session.order_id) if session.order_id else None,
        "payment_id": str(session.payment_id) if session.payment_id else None,
        "language": session.language,
        "relevant_tool_results": session.relevant_tool_results,
    }

    if target_unavailable:
        handoff = Handoff(
            session_id=session_id,
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
            conversation_summary=conversation_summary,
            shared_context=shared_context,
            success=False,
            return_to_agent=from_agent,
        )
        db.add(handoff)
        await db.flush()
        raise HandoffFailedError(
            to_agent,
            f"'{to_agent}' is not available for this session"
            + (
                " (merchant-facing specialist, no merchant context here)"
                if to_agent in MERCHANT_ONLY_AGENTS
                else ""
            ),
        )

    handoff = Handoff(
        session_id=session_id,
        from_agent=from_agent,
        to_agent=to_agent,
        reason=reason,
        conversation_summary=conversation_summary,
        shared_context=shared_context,
        success=True,
        return_to_agent=None,
    )
    db.add(handoff)

    session.previous_agent = from_agent
    session.current_agent = to_agent
    if conversation_summary:
        session.conversation_summary = conversation_summary
    await db.flush()

    return handoff


async def return_to_previous_agent(
    db: AsyncSession, session_id: uuid.UUID, *, reason: str = "specialist task complete"
) -> Handoff:
    """Hand control back from the current specialist to whichever agent
    it was handed off from (falls back to main_agent if that's somehow
    unknown, so a session can never get permanently stuck on a specialist).
    """
    session = await session_service.get_session(db, session_id)
    if session is None:
        raise ValueError(f"No agent session found with id {session_id}")

    from_agent = session.current_agent
    to_agent = session.previous_agent or "main_agent"

    handoff = Handoff(
        session_id=session_id,
        from_agent=from_agent,
        to_agent=to_agent,
        reason=reason,
        conversation_summary=None,
        shared_context={
            "cart_id": str(session.cart_id) if session.cart_id else None,
            "order_id": str(session.order_id) if session.order_id else None,
            "payment_id": str(session.payment_id) if session.payment_id else None,
        },
        success=True,
        return_to_agent=None,
    )
    db.add(handoff)

    session.current_agent = to_agent
    session.previous_agent = from_agent
    await db.flush()

    return handoff


async def get_handoff_history(db: AsyncSession, session_id: uuid.UUID) -> list[Handoff]:
    """A direct query (not a relationship-attribute access) on purpose —
    `session.handoffs` can be a cached, not-yet-loaded reference if the
    AgentSession object is already in this session's identity map from
    an earlier `db.get()`/`db.add()` call, which would trigger an
    unsafe synchronous lazy-load under async SQLAlchemy. See the
    Cart.items note in cart_service for the same underlying pitfall.
    """
    stmt = (
        select(Handoff)
        .where(Handoff.session_id == session_id)
        .order_by(Handoff.created_at)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
