"""
escalations.py — human-escalation requests for Bazaar Mitra (Day 7).

Stores short, human-readable summaries (never a full transcript, never passwords/OTPs/
PINs/account numbers) for the two situations the agent shouldn't try to resolve itself:
a payment/refund/order dispute, or a caller explicitly asking for a human. A human
reviews these in dashboard.py.

Uses the same SQLite file as db.py (bazaar_mitra.db), in a separate table.
"""

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "bazaar_mitra.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db_sync() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id TEXT,
            caller_name TEXT,
            reason TEXT,
            what_happened TEXT,
            what_agent_checked TEXT,
            urgency TEXT,
            caller_language TEXT,
            preferred_follow_up TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)


def _create_escalation_sync(
    caller_id: str,
    caller_name: Optional[str],
    reason: str,
    what_happened: str,
    what_agent_checked: str,
    urgency: str,
    caller_language: str,
    preferred_follow_up: str,
) -> str:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO escalations
            (caller_id, caller_name, reason, what_happened, what_agent_checked,
             urgency, caller_language, preferred_follow_up, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (caller_id, caller_name, reason, what_happened, what_agent_checked,
         urgency, caller_language, preferred_follow_up, now),
    )
    conn.commit()
    escalation_id = cur.lastrowid
    conn.close()
    return f"ESC-{escalation_id:04d}"


async def create_escalation(
    caller_id: str,
    reason: str,
    what_happened: str,
    what_agent_checked: str,
    urgency: str,
    caller_language: str,
    preferred_follow_up: str,
    caller_name: Optional[str] = None,
) -> str:
    """Creates an escalation record, returns a human-readable reference like 'ESC-0004'."""
    return await asyncio.to_thread(
        _create_escalation_sync,
        caller_id, caller_name, reason, what_happened, what_agent_checked,
        urgency, caller_language, preferred_follow_up,
    )


def _list_escalations_sync(status: Optional[str]) -> list[dict]:
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM escalations ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


async def list_escalations(status: Optional[str] = None) -> list[dict]:
    """status: 'open', 'resolved', or None for all."""
    return await asyncio.to_thread(_list_escalations_sync, status)


def _reference_to_id(reference_id: str) -> Optional[int]:
    try:
        return int(reference_id.strip().upper().removeprefix("ESC-"))
    except ValueError:
        return None


def _resolve_escalation_sync(reference_id: str) -> bool:
    escalation_id = _reference_to_id(reference_id)
    if escalation_id is None:
        return False
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE escalations SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (now, escalation_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


async def resolve_escalation(reference_id: str) -> bool:
    return await asyncio.to_thread(_resolve_escalation_sync, reference_id)