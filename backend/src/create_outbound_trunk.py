"""
create_outbound_trunk.py — one-time setup: register your Twilio SIP trunk with
LiveKit as a stored outbound trunk, so agent.py can dial through it by ID.

Run this ONCE after configuring Twilio (Termination SIP URI + credential list).
It prints a trunk ID like "ST_xxxxxxxx" — save that as LIVEKIT_OUTBOUND_TRUNK_ID
in your .env.local.

Reads from the environment (add these to .env.local before running):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (you already have these)
    TWILIO_TERMINATION_URI   e.g. "bazaar-mitra-outbound.pstn.twilio.com"
    TWILIO_PHONE_NUMBER      e.g. "+15105550100"  (the number you bought, E.164 format)
    SIP_AUTH_USERNAME        the username from the Twilio credential list you created
    SIP_AUTH_PASSWORD        the password from that same credential list
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo

# .env.local could reasonably be in a few places depending on how the project is laid
# out and where you run this from — check the likely spots instead of guessing one.
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

REQUIRED_VARS = [
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "TWILIO_TERMINATION_URI",
    "TWILIO_PHONE_NUMBER",
    "SIP_AUTH_USERNAME",
    "SIP_AUTH_PASSWORD",
]


def require_env() -> dict:
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        if not any(p.exists() for p in _CANDIDATE_ENV_PATHS):
            print("No .env.local found. Checked:")
            for p in _CANDIDATE_ENV_PATHS:
                print(f"  - {p}")
            print(f"Create it at: {ENV_PATH}")
        else:
            print(f"Using .env.local at: {ENV_PATH}")
        print()
        print("Missing required variable(s) in .env.local:")
        for name in missing:
            print(f"  - {name}")
        print()
        print("See the module docstring at the top of this file for what each one is.")
        sys.exit(1)
    return {name: os.environ[name] for name in REQUIRED_VARS}


async def main():
    env = require_env()

    async with api.LiveKitAPI() as lkapi:
        trunk = SIPOutboundTrunkInfo(
            name="Bazaar Mitra outbound trunk",
            address=env["TWILIO_TERMINATION_URI"],
            numbers=[env["TWILIO_PHONE_NUMBER"]],
            auth_username=env["SIP_AUTH_USERNAME"],
            auth_password=env["SIP_AUTH_PASSWORD"],
        )
        result = await lkapi.sip.create_sip_outbound_trunk(
            CreateSIPOutboundTrunkRequest(trunk=trunk)
        )

    print(f"Created outbound trunk: {result}")
    trunk_id = getattr(result, "sip_trunk_id", None)
    if trunk_id:
        print()
        print("Add this to your .env.local:")
        print(f"LIVEKIT_OUTBOUND_TRUNK_ID={trunk_id}")


if __name__ == "__main__":
    asyncio.run(main())