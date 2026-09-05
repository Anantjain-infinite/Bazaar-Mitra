"""
call_stats.py — call outcome tracking for Bazaar Mitra's Day 8 analytics dashboard.

Records exactly one row per call: whether it reached the track's success condition
(see agent.py — OUTCOME TRACKING comments and record_call_outcome()) or not. Stores
only non-sensitive metadata for the dashboard — never a transcript, never a password/
OTP/PIN/account number. caller_id is stored for internal debugging only and is never
returned by list_recent_calls(), which is what the public dashboard reads from.
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
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT NOT NULL,
            caller_id TEXT,
            call_type TEXT NOT NULL,   -- 'inbound' or 'outbound'
            outcome TEXT NOT NULL,     -- 'success' or 'failed'
            reason TEXT,               -- short tag: product_found / order_priced / escalated / no_resolution
            started_at TEXT,
            ended_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)


def _record_call_sync(
    call_id: str,
    caller_id: Optional[str],
    call_type: str,
    outcome: str,
    reason: Optional[str],
    started_at: Optional[str],
) -> None:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO calls (call_id, caller_id, call_type, outcome, reason, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (call_id, caller_id, call_type, outcome, reason, started_at, now),
    )
    conn.commit()
    conn.close()


async def record_call(
    call_id: str,
    caller_id: Optional[str],
    call_type: str,
    outcome: str,
    reason: Optional[str] = None,
    started_at: Optional[str] = None,
) -> None:
    """outcome must be 'success' or 'failed'."""
    await asyncio.to_thread(_record_call_sync, call_id, caller_id, call_type, outcome, reason, started_at)


def _get_summary_sync() -> dict:
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    success = conn.execute("SELECT COUNT(*) FROM calls WHERE outcome = 'success'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM calls WHERE outcome = 'failed'").fetchone()[0]
    conn.close()
    return {"total": total, "success": success, "failed": failed}


async def get_summary() -> dict:
    """Returns {'total': int, 'success': int, 'failed': int} — the three required numbers."""
    return await asyncio.to_thread(_get_summary_sync)


def _list_recent_sync(limit: int) -> list[dict]:
    conn = _get_conn()
    # Deliberately does NOT select caller_id — this is what the public dashboard
    # reads from, so it should be safe to display by construction, not just by
    # the dashboard template happening not to render a field.
    rows = conn.execute(
        """
        SELECT call_id, call_type, outcome, reason, started_at, ended_at
        FROM calls ORDER BY ended_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def list_recent_calls(limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(_list_recent_sync, limit)