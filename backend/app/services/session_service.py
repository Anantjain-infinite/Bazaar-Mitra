"""Session service — CRUD over `agent_sessions`.

This is the durable backing store for `AgentContext` (see
`app.agents.context`). The design principle carried over from the
existing voice agent's `CallState` (see `backend/src/agent.py`): a
specialist handoff creates a *new* agent instance with no memory of
anything stored only on `self`, so critical state must live somewhere
that survives that — this table, not a Python attribute.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentSession


async def create_session(
    db: AsyncSession,
    *,
    buyer_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
    channel: str = "voice",
    language: str = "en",
) -> AgentSession:
    session = AgentSession(
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        channel=channel,
        language=language,
        current_agent="main_agent",
        previous_agent=None,
        active=True,
        started_at=datetime.now(UTC),
        relevant_tool_results={},
    )
    db.add(session)
    await db.flush()
    return session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> AgentSession | None:
    return await db.get(AgentSession, session_id)


async def update_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    **fields,
) -> AgentSession:
    """Patch arbitrary known fields on a session (cart_id, order_id,
    payment_id, current_topic, conversation_summary,
    relevant_tool_results, language, ...). Unknown field names are
    rejected loudly rather than silently ignored.
    """
    session = await get_session(db, session_id)
    if session is None:
        raise ValueError(f"No agent session found with id {session_id}")

    valid_fields = {
        "buyer_id",
        "merchant_id",
        "language",
        "cart_id",
        "order_id",
        "payment_id",
        "current_topic",
        "conversation_summary",
        "relevant_tool_results",
        "current_agent",
        "previous_agent",
    }
    for key, value in fields.items():
        if key not in valid_fields:
            raise ValueError(f"Unknown AgentSession field: {key}")
        setattr(session, key, value)

    await db.flush()
    return session


async def merge_tool_result(
    db: AsyncSession, session_id: uuid.UUID, tool_name: str, result: dict
) -> AgentSession:
    """Cache the result of a recent tool call on the session, keyed by
    tool name — lets a specialist that just received a handoff avoid
    re-fetching what the main agent already looked up (spec section 11:
    "The user must NOT repeat the entire problem.").
    """
    session = await get_session(db, session_id)
    if session is None:
        raise ValueError(f"No agent session found with id {session_id}")
    session.relevant_tool_results = {**session.relevant_tool_results, tool_name: result}
    await db.flush()
    return session


async def end_session(db: AsyncSession, session_id: uuid.UUID) -> AgentSession:
    session = await get_session(db, session_id)
    if session is None:
        raise ValueError(f"No agent session found with id {session_id}")
    session.active = False
    session.ended_at = datetime.now(UTC)
    await db.flush()
    return session
