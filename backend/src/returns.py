"""
returns.py — return requests initiated by the Returns & Refunds Specialist (Day 9).

Stores a short record per return request — item, reason, eligibility outcome — never a
transcript. Same SQLite file as everything else (db.py, escalations.py, call_stats.py).
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
        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id TEXT,
            item TEXT NOT NULL,
            reason TEXT,
            eligible INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)


def _create_return_sync(caller_id: Optional[str], item: str, reason: str, eligible: bool) -> str:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO returns (caller_id, item, reason, eligible, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (caller_id, item, reason, 1 if eligible else 0, now),
    )
    conn.commit()
    return_id = cur.lastrowid
    conn.close()
    return f"RET-{return_id:04d}"


async def create_return(caller_id: Optional[str], item: str, reason: str, eligible: bool) -> str:
    """Creates a return request, returns a human-readable reference like 'RET-0003'."""
    return await asyncio.to_thread(_create_return_sync, caller_id, item, reason, eligible)