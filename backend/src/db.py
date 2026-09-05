"""
db.py — persistent memory for Bazaar Mitra

Stores one record per caller:
{
    "user_id": "string",
    "name": "string",
    "language_preference": "string",
    "facts": { "key": "value" },
    "last_interaction": "timestamp"
}

All calls are async-friendly wrappers around sqlite3 (which is blocking),
using asyncio.to_thread so they never block the voice agent's event loop.
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "bazaar_mitra.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db_sync() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            language_preference TEXT,
            facts TEXT DEFAULT '{}',
            last_interaction TEXT
        )
        """
    )
    conn.commit()
    conn.close()


async def init_db() -> None:
    """Call once at startup (e.g. in prewarm or at the top of the entrypoint)."""
    await asyncio.to_thread(_init_db_sync)


def _get_user_sync(user_id: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "name": row["name"],
        "language_preference": row["language_preference"],
        "facts": json.loads(row["facts"] or "{}"),
        "last_interaction": row["last_interaction"],
    }


async def get_user(user_id: str) -> Optional[dict]:
    """Look up a caller by user_id. Returns None if we've never spoken to them."""
    return await asyncio.to_thread(_get_user_sync, user_id)


def _save_user_sync(
    user_id: str,
    name: Optional[str],
    language_preference: Optional[str],
    facts: dict[str, Any],
) -> None:
    conn = _get_conn()
    existing = conn.execute(
        "SELECT name, language_preference, facts FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if existing:
        merged_facts = json.loads(existing["facts"] or "{}")
        merged_facts.update(facts or {})
        final_name = name if name else existing["name"]
        final_lang = language_preference if language_preference else existing["language_preference"]
    else:
        merged_facts = facts or {}
        final_name = name
        final_lang = language_preference

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO users (user_id, name, language_preference, facts, last_interaction)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name=excluded.name,
            language_preference=excluded.language_preference,
            facts=excluded.facts,
            last_interaction=excluded.last_interaction
        """,
        (user_id, final_name, final_lang, json.dumps(merged_facts), now),
    )
    conn.commit()
    conn.close()


async def save_user(
    user_id: str,
    name: Optional[str] = None,
    language_preference: Optional[str] = None,
    facts: Optional[dict[str, Any]] = None,
) -> None:
    """
    Save/update a caller record. New facts are merged into (not replacing)
    whatever we already had for that caller.
    """
    await asyncio.to_thread(_save_user_sync, user_id, name, language_preference, facts or {})