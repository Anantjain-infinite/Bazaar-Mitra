"""
trigger_outbound_call.py — place an outbound order-confirmation call with Bazaar Mitra.

Usage:
    python trigger_outbound_call.py +919876543210 \
        --shop "Sharma Kirana" \
        --order-summary "2kg atta, 1 wireless mouse - Rs. 513"

Checks the local do-not-call list before dialing (skips unless --force is passed),
then dispatches the agent with the call details as job metadata. agent.py reads that
metadata, places the actual SIP call, and opens with the required who/why/opt-out
disclosure once the callee picks up.

Reads LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, AGENT_NAME from .env.local
(same file your agent already uses).
"""

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path


from dotenv import load_dotenv
from livekit import api

import db

_SCRIPT_DIR = Path(__file__).resolve().parent
_RAW_CANDIDATES = [
    _SCRIPT_DIR / ".env.local",            # next to this script (backend/src/)
    _SCRIPT_DIR.parent / ".env.local",     # one level up (backend/) — common layout
    Path.cwd() / ".env.local",             # wherever you ran the command from
]
_seen = set()
_CANDIDATE_ENV_PATHS = []
for _p in _RAW_CANDIDATES:
    if _p not in _seen:
        _seen.add(_p)
        _CANDIDATE_ENV_PATHS.append(_p)


def _find_env_path() -> Path:
    for candidate in _CANDIDATE_ENV_PATHS:
        if candidate.exists():
            return candidate
    # None exist yet — default to the backend/ layout, the most common one.
    return _CANDIDATE_ENV_PATHS[1]


ENV_PATH = _find_env_path()
load_dotenv(ENV_PATH)

AGENT_NAME = os.getenv("AGENT_NAME", "my-agent")


def normalize_phone(phone_number: str) -> str:
    """Matches agent.py's normalize_phone exactly, so the do-not-call check
    and agent.py's own DB lookups always agree on the same caller_id."""
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


async def main():
    parser = argparse.ArgumentParser(description="Trigger an outbound Bazaar Mitra order-confirmation call")
    parser.add_argument("phone_number", help="E.164 phone number to call, e.g. +919876543210")
    parser.add_argument("--shop", default="your local shop", help="Shop the call is on behalf of")
    parser.add_argument("--order-summary", required=True, help="e.g. '2kg atta, 1 wireless mouse - Rs. 513'")
    parser.add_argument("--force", action="store_true", help="Call even if this number is on the do-not-call list")
    args = parser.parse_args()

    await db.init_db()
    caller_id = normalize_phone(args.phone_number)
    existing = await db.get_user(caller_id)
    if existing and existing.get("facts", {}).get("do_not_call") and not args.force:
        print(f"Caller {caller_id} has opted out of calls (do_not_call=True). Skipping.")
        print("Pass --force to override, if you're sure.")
        return

    metadata = json.dumps({
        "phone_number": args.phone_number,
        "reason": "order_confirmation",
        "context": {
            "shop": args.shop,
            "order_summary": args.order_summary,
        },
    })

    room_name = f"outbound-{uuid.uuid4().hex[:8]}"

    async with api.LiveKitAPI() as lkapi:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )

    print(f"Dispatched: calling {args.phone_number} about \"{args.order_summary}\" (room {room_name})")
    print("Watch your agent's terminal for logs as the call connects.")


if __name__ == "__main__":
    asyncio.run(main())