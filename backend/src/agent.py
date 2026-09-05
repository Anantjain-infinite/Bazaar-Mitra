import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    get_job_context,
    room_io,
    tokenize,
)
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import call_stats
import catalogue
import db
import escalations
import returns
import returns_policy

# Make the sibling `app` package (backend/app/, the FastAPI commerce
# backend built in Phases 0-8) importable regardless of how this script
# is invoked — `uv run src/agent.py` only puts src/ on sys.path by
# default, not its parent (backend/), so this is made explicit rather
# than relying on that.
#
# Everything from `app` is imported at MODULE level below (not inside
# functions as `import module_name`) so it's always in scope when needed —
# but every import is qualified (`app.agents.main_agent`, etc.), never a
# bare `from app.db.models import AgentSession`, because this file already
# uses LiveKit's own `AgentSession` class everywhere (the voice pipeline
# session type). Qualifying every commerce import avoids that collision
# entirely rather than requiring careful naming discipline everywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.agents.buyer_agent as commerce_buyer_agent
import app.agents.context as commerce_context
import app.agents.main_agent as commerce_main_agent
import app.agents.order_support_agent as commerce_order_support_agent
import app.agents.payments_agent as commerce_payments_agent
import app.services.buyer_service as commerce_buyer_service
import app.services.handoff_service as commerce_handoff_service
import app.services.session_service as commerce_session_service
from app.db.session import AsyncSessionLocal as CommerceDBSession

logger = logging.getLogger("agent")

# Stored LiveKit outbound trunk ID (looks like "ST_xxxxxxxx"), created once with
# create_outbound_trunk.py. Required for agent-initiated outbound calls (Day 6).
OUTBOUND_TRUNK_ID = os.getenv("LIVEKIT_OUTBOUND_TRUNK_ID")

load_dotenv(".env.local")


@dataclass
class CallState:
    """
    Shared across EVERY agent in the session (the main Assistant, the Returns
    Specialist, and any future specialist) — this is what survives a Day 9 handoff.
    Each new Agent subclass instance is otherwise a blank slate with no memory of
    caller_id or anything else, since handoffs create a fresh instance. Every tool
    below receives this via RunContext[CallState] instead of storing state on self.
    """

    caller_id: str = ""
    outcome_achieved: bool = False
    outcome_reason: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # The one piece of commerce state that lives on CallState — everything
    # else (cart, order, payment, current specialist, conversation summary)
    # lives in the `agent_sessions` DB row this id points to, not here, for
    # the exact same reason caller_id/outcome_reason above do: a specialist
    # handoff creates a brand-new Agent instance with no memory of `self`.
    # See app.agents.context.AgentContext / _ensure_commerce_session below.
    commerce_session_id: Optional[str] = None

    def mark_outcome(self, reason: str) -> None:
        # First success wins if more than one happens in a call — keeps the
        # Day 8 "reason" meaningful instead of just recording the last thing that fired.
        if not self.outcome_achieved:
            self.outcome_achieved = True
            self.outcome_reason = reason


# Change this prompt to change what your voice agent does.
# See README.md for example prompts (customer support, language tutor, receptionist).
SYSTEM_PROMPT = """
IDENTITY
You are Bazaar Mitra, a friendly AI Voice Shopping Assistant for Local Commerce in India.
You help customers discover nearby local shops, compare products, answer shopping-related questions, and guide users through purchases.
You speak naturally and politely like a helpful store assistant.

OBJECTIVES
A successful conversation should achieve one or more of these goals:
1. Help the customer find products from nearby local businesses.
2. Compare available options, prices, and shops when reliable information exists.
3. Guide the customer towards contacting the seller or completing a purchase through the seller's official process.

MEMORY
Whatever is known about this caller (name, language preference, past facts) is already
given to you at the start of each conversation as background information — you do not
need to look it up yourself to greet them.

You have three tools for managing caller memory:
- identify_caller: call this if a caller you don't already recognize wants to be
  remembered across calls, or asks whether you remember them. Ask for their phone
  number first, then call this with it — it becomes their ID for the rest of the call,
  and recovers any record saved under that number before. Don't ask for a phone number
  by default on every new call; only when it's relevant or the caller wants it.
- lookup_caller_history: call this ONLY after the caller has said something in this
  conversation (e.g. they ask "what do you have on me?" or you want to double-check a
  fact later on). Never call any tool before the caller has spoken at all in this session.
- save_caller_info: call this to remember a caller's name, language preference, or shopping
  facts (past orders, usual quantities, preferred delivery slot, preferred shop) for next time.

Facts worth remembering for Local Commerce: what they're currently shopping for
(product, type, budget), past orders, usual quantities ordered, preferred delivery slot,
preferred shop/vendor.

PROACTIVELY OFFER TO REMEMBER (don't wait to be asked)
Saving only happens if you actually call save_caller_info — a good conversation about a
product is NOT automatically remembered just because it happened. So: when the caller is
wrapping up (says thanks, "no that's all", goodbye, etc.) and you learned something in
this call worth remembering for next time (what they were shopping for, a preferred shop,
their name) that hasn't been saved yet, ask ONCE before they go: "Should I remember this
for next time you call?" If they say yes, save it (following the CONSENT RULE below) —
then say goodbye. If they say no or don't respond clearly, just say goodbye normally.
Don't ask this every single turn — once, near the end of the call, is enough.

CONSENT RULE (hard rule, never skip it):
Before calling save_caller_info, you must first tell the caller in their own words that you
would like to remember this for next time, and get a clear "yes". If they say no, or don't
clearly agree, do NOT call save_caller_info for that information. You may still continue
helping them normally.

GREETING RETURNING CALLERS
If you were given an existing record for this caller at the start of the conversation,
greet them by name and refer naturally to what you last discussed, e.g. "Namaste Ramesh,
last time you ordered 2kg of atta from Sharma Kirana. Would you like the same again?"

GREETING NEW CALLERS
If no record was found for this caller, introduce yourself as Bazaar Mitra, briefly explain
what you can help with, and ask for their name so you can address them personally during
the call — e.g. "I'm Bazaar Mitra. Before we start, may I know your name?" Do this as part
of your very first greeting, not later. Simply asking for and using their name during the
call does NOT need consent — the CONSENT RULE only applies when you want to save it to the
database for next time (see PROACTIVELY OFFER TO REMEMBER above).

CATALOGUE & ORDER TOTALS (quick lookups only — see SHOPPING & CHECKOUT below for an
actual purchase)
You have real catalogue data through two tools for quick informational lookups. Never
state a price, stock status, or order total from memory or a guess.

- lookup_products: call this as soon as a caller names a product or category they're
  interested in (e.g. "mouse", "atta", "notebooks"), even before they've mentioned a shop
  or quantity, so you can tell them what's actually available and what it costs.
- compute_order_total: call this once the caller has given you specific products AND
  quantities (e.g. "2kg atta and a wireless mouse"). This gives a PRICE ESTIMATE only —
  it never places or confirms an order. If the caller wants to actually BUY something,
  move to SHOPPING & CHECKOUT below instead of stopping at an estimate.

Both tools return an "as_of" date. Mention it naturally when you quote a price or total
(e.g. "as of Aug 9, that's ₹449") so the caller knows how fresh the number is — you don't
need to repeat it on every single sentence, once per quote is enough.

If a tool returns ok: False, the catalogue service is unavailable. Say so plainly and do
NOT guess a price or stock number instead — e.g. "I can't reach our price list right now,
please try again in a bit or contact the shop directly." Treat this like the escalation
script below.

If compute_order_total returns items in "unresolved", tell the caller specifically which
items and why — don't just total up what did resolve and stay silent about the rest.

SHOPPING & CHECKOUT (an actual purchase, with real payment)
When a caller wants to actually BUY something — not just get a price estimate — use this
flow instead of stopping at compute_order_total:

1. find_and_compare_products: call this with what the caller wants to buy. It searches
   ALL local merchants for real matches, picks the best available (cheapest in-stock)
   option, and explains why — including if a cheaper option exists but is out of stock.
   Tell the caller the pick and the reason in your own words.
2. add_item_to_cart: once the caller agrees with a product (the one just found, or a
   different one they name from the comparison), add it. Use the product's id from the
   previous tool result — never ask the caller to read out an id, they never see one.
   If the tool result mentions cross-sell/upsell suggestions, you may offer ONE naturally
   ("people often get a mouse pad with that — want to add one?") — never add it yourself
   without them saying yes.
3. checkout: once the caller is done adding items, call this. It creates the order and
   returns the exact total plus a policy check result. State the total plainly.
   - If the policy check failed (policy.allowed is false), tell the caller plainly why
     (the reasons are in the result) — do NOT proceed to confirm_purchase.
   - If checkout returns a price_changed or out_of_stock error, explain it and do not
     proceed — see GUARDRAILS.
4. confirm_purchase: ONLY call this after the caller has clearly said yes to the exact
   total checkout just gave them ("yes", "confirm", "haan", "proceed", "go ahead" — see
   CONFIRMATION below). This is the explicit-approval gate; never skip it, never call it
   on an ambiguous response like "maybe" or silence.
5. start_payment: once confirmed, call this to prepare the payment. It never asks for or
   collects card numbers, UPI PINs, OTPs, or any payment credential over the call — that
   would violate the GUARDRAILS below regardless of how this tool is used. Relay exactly
   what the tool's "message_for_caller" field says (it explains how they'll actually pay,
   e.g. via the Bazaar Mitra app/website using their order reference) — do not improvise
   a different payment method yourself.

CONFIRMATION (hard rule for confirm_purchase, never skip it)
Accepted confirmations: "yes", "confirm", "proceed", "pay", "haan", "हाँ", "go ahead". If
the caller says something ambiguous like "maybe" or doesn't respond clearly, do NOT call
confirm_purchase — ask them to confirm plainly first.

PAYMENT ISSUES
If the caller asks about a payment that failed, wants to check payment status, or wants
to retry a payment, use transfer_to_payments_specialist — this hands the conversation to
a specialist who can actually check and retry it, instead of you guessing. Tell the
caller you're connecting them first, briefly.

ORDER STATUS & CANCELLATION
If the caller asks about the status of an order they placed, or wants to cancel one
before it's been paid for, use transfer_to_order_support_specialist.

KNOWLEDGE
You can:
- Explain products.
- Recommend products based on user needs.
- Compare features.
- Look up real prices and stock via lookup_products, and compute order estimates via
  compute_order_total (see CATALOGUE & ORDER TOTALS above).
- Actually place and pay for an order via the SHOPPING & CHECKOUT flow above, when the
  caller wants to buy something for real.
- Help users understand delivery options if provided.
- Help locate nearby businesses if information is available.

You cannot:
- State a price or stock status without calling a real tool for it.
- Invent delivery dates.
- Mark or claim a payment successful yourself — only the system's own verification does
  that (see start_payment above and GUARDRAILS below).
- Pretend to access live databases.

LANGUAGE
Always mirror the user's language.
If the user mixes Hindi and English, respond in the same style.
If they speak only Hindi, reply in Hindi.
If they speak only English, reply in English.
Use simple conversational language suitable for voice conversations.
Also the text of the response should be in the same language as the user.

OUTBOUND CALLS
Sometimes YOU are calling the customer, not the other way around — you'll be told this
explicitly in your opening instructions for that call, along with why you're calling. This
is different from an inbound call: the person didn't ask for this call and doesn't know who
you are yet, so open carefully.

Within your very first two sentences, before anything else, you MUST say:
1. Who is calling — Bazaar Mitra — and which shop it's on behalf of, if you know it.
2. Why you're calling, specifically (e.g. confirming a particular order).
3. That they can ask you to stop calling at any time and you'll comply immediately.

Do not wait for them to speak first on an outbound call — you called them, so you open.

If at ANY point — opening or later — the caller says something like "stop calling me",
"don't call again", "remove my number", or similar: say a brief goodbye acknowledging it,
then call opt_out_of_calls. Do this immediately, no matter what else is happening in the
call. This applies on inbound calls too if they ask you to stop calling them in future.

RETURNS & REFUNDS
If the caller wants to return an item, asks about the return policy, or asks about a
refund timeline for something they're returning, use transfer_to_returns_specialist —
this hands the conversation to a specialist who actually knows the return rules, instead
of you guessing. Tell the caller you're connecting them first, briefly, e.g. "Sure, let
me connect you with our returns and refunds specialist" — then call the tool.

Do NOT use this handoff for a payment/order dispute (wrong charge, promised refund never
arrived) or an explicit request for a human — those still go through create_escalation
(see HUMAN ESCALATION below), since those genuinely need a person, not another AI.

GUARDRAILS

Never:
- Claim a payment succeeded, or an order is confirmed/placed, without the corresponding
  tool actually reporting that back to you — never based on your own belief or something
  the caller asserts happened.
- Skip the explicit CONFIRMATION step before calling confirm_purchase, no matter how
  clearly the caller states their overall intent ("find me a mouse and just buy it" is
  not itself a confirmation — you still show the total from checkout and get an explicit
  yes before confirm_purchase).
- Proceed with checkout or confirm_purchase if the tool result says the price changed or
  an item is out of stock — explain what changed and ask what they'd like to do instead.
- Claim a product is in stock without verified information.
- Promise a delivery date.
- Make up prices.
- Pretend to be a human employee.
- Collect passwords, OTPs, PINs, UPI PINs, or any payment credential — including during
  start_payment, which never asks for these; payment completion always happens outside
  the call (see SHOPPING & CHECKOUT step 5).
- Ask for sensitive financial information.
- Save any information without first asking and getting a clear "yes" (see CONSENT RULE).
- Continue calling, or call again, someone who has asked you to stop (see OUTBOUND CALLS).
- Create a human escalation without asking and getting a clear "yes" first (see
  HUMAN ESCALATION below).

If asked something outside your capabilities, politely say in user's language of last message:

"I'm sorry, I can't verify that information. Please contact the seller directly for confirmation."

Escalation Script (for things that just aren't worth a human ticket)

If the customer asks about delivery confirmation, or anything else you genuinely can't
verify that ISN'T one of the two situations in HUMAN ESCALATION below, say in the
user's language of last message:

"I can't verify that information myself. Please contact the shop or customer support for confirmation."

HUMAN ESCALATION
For exactly two situations, don't just give the line above — actively create a request
for a human to follow up, using the create_escalation tool:
1. A payment, refund, or order dispute — the caller says they were charged wrong, wants
   a refund, or disputes what an order should have cost or contained.
2. The caller explicitly asks to speak to a human, the shop owner, or says something
   like "I don't want to talk to a bot" / "let me talk to a person".

Nothing else should create an escalation — for anything else unresolved, use the
Escalation Script above instead.

ESCALATION CONSENT RULE (hard rule, never skip it):
Before calling create_escalation, tell the caller PLAINLY what you're about to send —
e.g. "I'll pass along your name, what happened, and how urgent it seems, so someone can
call you back — is that okay?" — and get a clear "yes". If they say no, do NOT call
create_escalation; give them the Escalation Script line instead.

When you do create one, gather these (leave genuinely unknown ones as "unknown" —
never invent them):
- who needs help: their name if you have it, and a number to reach them if they want a
  callback
- what happened: a short, factual summary in your own words — not a transcript
- what you already checked: e.g. "looked up the order in the catalogue — atta was
  ₹42/kg as of Aug 9" or "no matching order found in the catalogue"
- urgency: low / medium / high — your judgment from how they describe it
- their language preference
- how they'd like to be followed up with (call back, any time is fine, a specific time)
NEVER include a password, OTP, PIN, or account number in any of this — you should never
have collected those from the caller in the first place.

After create_escalation succeeds, it gives you a reference ID — tell the caller that ID
and what happens next, honestly. E.g. "Someone from the shop will follow up with you —
I can't promise exactly when, but here's your reference: ESC-0004." Never promise an
immediate reply unless you actually know that's true.

STYLE
- Speak naturally.
- Keep responses under 20 words whenever possible.
- Ask one question at a time.
- Be polite and friendly.
- Never overwhelm the user with long explanations.
- If the user is silent, gently ask if they are still there.
"""

RETURNS_SPECIALIST_PROMPT = """
IDENTITY
You are the Returns & Refunds Specialist for Bazaar Mitra. You have ONE job: helping
callers understand and act on the return/refund policy. You are not the general shopping
assistant — don't try to look up general products or compute order totals, that isn't
your job; if the caller asks about something outside returns/refunds, say so plainly and
that you're the returns specialist.

WHAT YOU CAN DO
- Explain the return policy in plain language when asked (return window, refund method,
  refund timeline).
- Check whether a specific item is eligible for return using check_return_eligibility —
  never guess eligibility yourself, always call the tool. Ask for what's needed (item,
  how many days since purchase, whether it's been opened/used) if you don't know yet.
- Start a return request using initiate_return, once you know the item and its
  eligibility from check_return_eligibility.
- Escalate to a human via create_escalation if the caller has an actual DISPUTE (e.g.
  "I already returned this and never got my refund", "you charged me twice") or
  explicitly asks for a human. Same consent rule as ever: tell them what you're sending
  and get a clear yes first.

LANGUAGE
Mirror the caller's language, same as the rest of Bazaar Mitra.

GUARDRAILS
Never invent an eligibility answer — always call check_return_eligibility. Never promise
an exact refund date beyond what the tool tells you. Never collect passwords, OTPs, PINs,
or account numbers.

STYLE
Keep responses short and clear, one question at a time — same conversational style as
the rest of Bazaar Mitra.
"""

PAYMENTS_SPECIALIST_PROMPT = """
IDENTITY
You are the Payments Specialist for Bazaar Mitra. You have ONE job: helping callers
understand and resolve a problem with a payment on an order placed in THIS call or a
prior one. You are not the general shopping assistant — if the caller wants to start a
new purchase, hand them back with return_to_main_assistant and let the main assistant
handle it.

WHAT YOU CAN DO
- Check the status of the current payment with get_payment_status.
- Show the full attempt history (e.g. "attempt 1 failed, attempt 2 succeeded") with
  get_payment_history — use this when the caller wants to understand what happened, not
  just the current state.
- Retry a failed payment with retry_payment. This creates a genuinely fresh payment
  attempt — it can never mark the OLD failed attempt as successful, and it can never
  mark ANY attempt successful itself; only the system's own server-side verification
  does that, after the caller actually completes payment through the app/website.
- Hand back to the main assistant with return_to_main_assistant once the payment issue
  is resolved or there's nothing more to do here.

WHAT YOU MUST NEVER DO
- Never say a payment succeeded, or mark one as successful, based on anything other than
  what get_payment_status/retry_payment actually report back to you.
- Never collect a card number, UPI PIN, OTP, or any other payment credential — payment
  completion always happens outside this call, through the app/website.
- Never guess why a payment failed if the tool didn't give you a reason — say plainly
  that the specific reason isn't available.

LANGUAGE
Mirror the caller's language, same as the rest of Bazaar Mitra.

STYLE
Introduce yourself briefly the first time you speak ("Hi, I'm the payments specialist —
I can see..."), then keep responses short and clear, same conversational style as the
rest of Bazaar Mitra.
"""

ORDER_SUPPORT_SPECIALIST_PROMPT = """
IDENTITY
You are the Order Support Specialist for Bazaar Mitra. You have ONE job: order status
and cancellation for orders placed in THIS call or a prior one. You are not the general
shopping assistant, and you are not the payments or returns specialist — if the caller's
issue is actually about a failed/stuck payment, or about returning something they
already received, say so and let the main assistant route them correctly; don't try to
handle it yourself.

WHAT YOU CAN DO
- Check an order's current status with get_order_status.
- Cancel an order with cancel_order — but ONLY works before any payment has actually
  gone through (the tool will tell you plainly if it can't be cancelled directly, e.g.
  because it's already been paid for; in that case explain that and suggest the returns
  process instead, or hand off with return_to_main_assistant so the main assistant can
  route to returns).
- Give a general fulfillment/delivery status with get_fulfillment_status — be upfront
  that detailed shipment tracking isn't available yet if the tool says so; never invent
  a delivery date or courier status it didn't give you.
- Hand back to the main assistant with return_to_main_assistant once done.

GUARDRAILS
Never invent an order status, a cancellation outcome, or a delivery estimate — always
get it from the tool. Never collect passwords, OTPs, PINs, or payment credentials.

LANGUAGE
Mirror the caller's language, same as the rest of Bazaar Mitra.

STYLE
Introduce yourself briefly the first time you speak, then keep responses short and
clear, same conversational style as the rest of Bazaar Mitra.
"""


@asynccontextmanager
async def commerce_db():
    """A DB session for the new FastAPI commerce backend's services
    (app.services.*, app.agents.*), usable here even though this is a
    LiveKit worker process, not a FastAPI request — mirrors
    app.db.session.get_db's rollback-on-exception behavior. Every
    commerce tool below opens one of these per call rather than sharing
    a long-lived session across the whole call.
    """
    async with CommerceDBSession() as commerce_session:
        try:
            yield commerce_session
        except Exception:
            await commerce_session.rollback()
            raise


async def _ensure_commerce_session(context: "RunContext[CallState]") -> str:
    """Lazily create the DB-backed commerce AgentSession (app_sessions
    table) the first time a caller does something commerce-related in
    this call, bridging their phone-number caller_id to a real Buyer
    row (commerce_buyer_service.get_or_create_buyer_by_phone). The
    resulting session id is cached on CallState so every later commerce
    tool call — and a handoff to a new specialist Agent instance — reuses
    the same underlying commerce session rather than creating a new one.

    This is a deliberate simplification worth knowing about: it keys the
    Buyer strictly off whatever `caller_id` is set at the moment the
    FIRST commerce tool fires. If a caller's identity is established or
    changed later in the same call, that doesn't retroactively change
    which Buyer this commerce session is tied to.
    """
    if context.userdata.commerce_session_id:
        return context.userdata.commerce_session_id

    async with commerce_db() as cdb:
        buyer = await commerce_buyer_service.get_or_create_buyer_by_phone(
            cdb,
            context.userdata.caller_id or f"voice-{uuid.uuid4().hex[:10]}",
            preferred_language="en",
        )
        commerce_session = await commerce_session_service.create_session(
            cdb, buyer_id=buyer.id, channel="voice", language="en"
        )
        await cdb.commit()
        context.userdata.commerce_session_id = str(commerce_session.id)
    return context.userdata.commerce_session_id


class SharedToolsMixin:
    """
    Tools usable from ANY agent in the session, not just one — currently create_escalation
    and opt_out_of_calls. Mixed into both Assistant and ReturnsSpecialist so a caller who
    says "stop calling me" or needs a human is never stuck depending on which agent happens
    to be active. Needs context.userdata (a CallState) at call time, same as every other tool.
    """

    @function_tool
    async def create_escalation(
        self,
        context: RunContext[CallState],
        reason: str,
        what_happened: str,
        what_agent_checked: str,
        urgency: str,
        caller_language: str,
        preferred_follow_up: str,
    ) -> dict:
        """Create a request for a human to follow up with this caller.

        Only call this for (1) a payment, refund, or order dispute, or (2) the caller
        explicitly asking to speak to a human or the shop owner — and only AFTER you've
        told the caller what you're about to send and gotten a clear "yes" (see the
        ESCALATION CONSENT RULE). Never put a password, OTP, PIN, or account number in
        any argument here.

        Args:
            reason: Short label for why: "payment_or_order_dispute" or "requested_human".
            what_happened: A short, factual summary in your own words — not a transcript.
                E.g. "Caller says they were charged for 2kg atta but only received 1kg."
            what_agent_checked: What you already looked into, e.g. "Looked up atta in
                the catalogue — ₹42/kg as of Aug 9 — but can't see delivered quantities."
            urgency: "low", "medium", or "high" — your judgment from how they describe it.
            caller_language: The language the caller has been speaking, e.g. "Hindi".
            preferred_follow_up: How they'd like to be followed up with, e.g. "call back
                on this number", "any time is fine", "evenings only".
        """
        caller_id = context.userdata.caller_id
        existing = await db.get_user(caller_id)
        caller_name = existing.get("name") if existing else None

        reference_id = await escalations.create_escalation(
            caller_id=caller_id,
            caller_name=caller_name,
            reason=reason,
            what_happened=what_happened,
            what_agent_checked=what_agent_checked,
            urgency=urgency,
            caller_language=caller_language,
            preferred_follow_up=preferred_follow_up,
        )
        logger.info(
            f"Created escalation {reference_id} for caller {caller_id}: {reason}"
        )
        context.userdata.mark_outcome("escalated")
        return {"reference_id": reference_id}

    @function_tool
    async def opt_out_of_calls(self, context: RunContext[CallState]) -> None:
        """Mark this caller as do-not-call and end the call.

        Call this if the caller says something like "stop calling me", "don't call
        again", or "remove my number" — on an outbound call or an inbound one. Before
        calling this, say a brief goodbye acknowledging you won't call again; the call
        ends right after you finish speaking, so say that goodbye in the same turn.
        Future outbound calls to this caller should be skipped after this.
        """
        caller_id = context.userdata.caller_id
        await db.save_user(caller_id, facts={"do_not_call": True})
        logger.info(f"Caller {caller_id} opted out of future outbound calls")
        await context.wait_for_playout()  # let the goodbye finish playing first
        await _hangup_call()


class Assistant(SharedToolsMixin, Agent):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(instructions=SYSTEM_PROMPT, chat_ctx=chat_ctx)

    @function_tool
    async def lookup_caller_history(self, context: RunContext[CallState]) -> dict:
        """Look up whether this caller has spoken with Bazaar Mitra before.

        You already receive a summary of what's known about this caller at the start of
        the conversation, so you normally do NOT need to call this. Only call it later in
        the conversation, after the caller has said something, if you need to re-confirm
        or refresh what's on file (e.g. they ask what you remember about them).

        Returns a dict describing what is on file (name, language_preference, facts,
        last_interaction), or {"found": False} if there is no record for this caller yet.
        """
        caller_id = context.userdata.caller_id
        user = await db.get_user(caller_id)
        if user is None:
            logger.info(f"No existing record for caller {caller_id}")
            return {"found": False}
        logger.info(f"Found existing record for caller {caller_id}")
        return {"found": True, **user}

    @function_tool
    async def save_caller_info(
        self,
        context: RunContext[CallState],
        name: Optional[str] = None,
        language_preference: Optional[str] = None,
        facts: Optional[dict[str, Any]] = None,
    ) -> str:
        """Save or update what you know about this caller, for future conversations.

        Only call this AFTER you have told the caller you'd like to remember this and they
        have clearly agreed. Never call this to store passwords, OTPs, PINs, UPI PINs, or
        other payment/account credentials.

        Args:
            name: The caller's name, if they shared it and agreed you can remember it.
            language_preference: Preferred language/locale, e.g. "hi-IN" or "en-IN".
            facts: Local Commerce facts to remember, e.g. {"past_orders": "2kg atta,
                1L mustard oil", "usual_quantities": "2kg atta weekly",
                "preferred_delivery_slot": "evening", "preferred_shop": "Sharma Kirana"}.
                New values are merged with what is already saved, not replaced.
        """
        caller_id = context.userdata.caller_id
        await db.save_user(
            caller_id, name=name, language_preference=language_preference, facts=facts
        )
        logger.info(f"Saved info for caller {caller_id}: name={name}, facts={facts}")
        return "saved"

    @function_tool
    async def identify_caller(
        self, context: RunContext[CallState], phone_number: str
    ) -> dict:
        """Identify (or start tracking) this caller by a phone number they give you.

        Use this when you don't already recognize the caller from the start-of-call
        context and it would help to recognize them on future calls — for example, they
        ask "do you remember me?", or they want you to remember details for next time.
        Ask for their phone number first, get it, then call this tool with it.

        This makes the phone number the caller's stable ID for the rest of THIS call: any
        later save_caller_info call will save under this phone number, and if a record
        already existed under this number (from a previous call), you'll get it back here.

        Args:
            phone_number: The phone number the caller told you, digits as spoken/heard.
        """
        normalized = normalize_phone(phone_number)
        if not normalized:
            return {
                "found": False,
                "error": "that didn't look like a valid phone number",
            }

        context.userdata.caller_id = normalized
        user = await db.get_user(normalized)
        if user is None:
            logger.info(
                f"No existing record when identifying caller by phone {normalized}"
            )
            return {"found": False}
        logger.info(f"Identified returning caller by phone {normalized}")
        return {"found": True, **user}

    @function_tool
    async def lookup_products(
        self, context: RunContext[CallState], query: str, shop: Optional[str] = None
    ) -> dict:
        """Look up real price and stock for a product or category in Bazaar Mitra's
        catalogue.

        ALWAYS call this before telling a caller a specific price or whether something is
        in stock — never guess or state a remembered price. Call it as soon as the caller
        names a product or category (e.g. "mouse", "atta", "notebooks"), even before they
        mention a shop or quantity.

        If the tool result has ok: False, the catalogue service is unavailable right now —
        tell the caller plainly and do not invent a price or stock number instead.

        Args:
            query: The product name or category the caller is asking about, e.g.
                "wireless mouse", "atta", "stationery". Partial names are fine.
            shop: The shop name to filter to, only if the caller named one specifically.
        """
        result = await catalogue.lookup_products(query, shop)
        if result.get("ok") and result.get("matches"):
            context.userdata.mark_outcome("product_found")
        return result

    @function_tool
    async def compute_order_total(
        self, context: RunContext[CallState], items: list[dict]
    ) -> dict:
        """Compute a price estimate for specific products and quantities, using real
        catalogue prices and stock. Never calculate or state a total yourself without
        calling this.

        Call this once the caller has told you specific products AND quantities they want
        (e.g. "2kg atta and a wireless mouse"). This gives a PRICE ESTIMATE only — it does
        NOT place or confirm an order (you can never confirm an order yourself).

        If the tool result has ok: False, the catalogue service is unavailable right now —
        tell the caller plainly and do not invent numbers instead. If it lists items under
        "unresolved", tell the caller specifically which items couldn't be priced and why
        (not found, or not enough stock) rather than silently leaving them out.

        Args:
            items: A list of items, each like {"product": "atta", "quantity": 2, "shop":
                "Sharma Kirana"}. "shop" is optional per item — omit it to automatically
                use the cheapest shop that has it. "quantity" is in the product's natural
                unit (kg, litre, piece, pack, etc.) as returned by lookup_products.
        """
        result = await catalogue.compute_order_total(items)
        if result.get("ok") and result.get("line_items"):
            context.userdata.mark_outcome("order_priced")
        return result

    @function_tool
    async def transfer_to_returns_specialist(
        self, context: RunContext[CallState]
    ) -> tuple[Agent, str]:
        """Transfer the conversation to the Returns & Refunds Specialist.

        Call this when the caller wants to return an item, asks about the return policy,
        or asks about a refund timeline for something they're returning — anything about
        returning something they already bought.

        Do NOT use this for a payment/order dispute (wrong charge, promised refund never
        arrived) or an explicit request for a human — those go through create_escalation
        instead (see HUMAN ESCALATION), since those need an actual person, not another AI.
        """
        specialist = ReturnsSpecialist(
            chat_ctx=self.chat_ctx.copy(exclude_instructions=True)
        )
        return (
            specialist,
            "Sure — let me connect you with our returns and refunds specialist.",
        )

    # --- Real purchase flow — see SHOPPING & CHECKOUT in the system prompt for the
    # step-by-step this is meant to be used in. Each tool opens its own short-lived
    # commerce_db() session and always loads a fresh AgentContext from the DB rather than
    # trusting anything cached, per app.agents.context's design (a specialist handoff
    # creates a brand new Agent instance with no memory of `self`).

    @function_tool
    async def find_and_compare_products(
        self, context: RunContext[CallState], query: str, city: Optional[str] = None
    ) -> dict:
        """Search for a product the caller wants to BUY across all local merchants, pick
        the best available (cheapest in-stock) option, and explain why — real prices and
        stock only, never invented. This does not add anything to a cart — call
        add_item_to_cart next once the caller agrees with a product.

        Args:
            query: What the caller wants, in their own words, e.g. "wireless mouse under
                1000 rupees" or "5kg atta".
            city: The caller's city, only if they've mentioned one.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            result = await commerce_buyer_agent.discover_and_compare(
                cdb, ctx, query, city=city
            )
        if result.get("ok"):
            context.userdata.mark_outcome("product_found")
        return result

    @function_tool
    async def add_item_to_cart(
        self, context: RunContext[CallState], product_id: str, quantity: int = 1
    ) -> dict:
        """Add a product to the caller's cart. Use the product id from a previous
        find_and_compare_products (or view_cart) result — never ask the caller to read
        out an id, they never see or say one; you already have it from the earlier tool
        result.

        Args:
            product_id: The product's id, from a prior tool result.
            quantity: How many, default 1.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            # find_and_compare_products deliberately doesn't lock in a merchant (the
            # caller may pick any product from the comparison, each potentially from a
            # different shop) — so make sure the cart's merchant actually matches
            # whichever product they just chose before adding it.
            from app.services import catalog_service

            product = await catalog_service.get_product_by_id(
                cdb, uuid.UUID(product_id)
            )
            if product is None:
                return {"ok": False, "error": f"No product found with id {product_id}"}
            if ctx.merchant_id != product.merchant_id:
                select_result = await commerce_main_agent.select_merchant(
                    cdb, ctx, product.merchant_id
                )
                if not select_result.get("ok"):
                    return select_result
            return await commerce_main_agent.add_to_cart(
                cdb, ctx, uuid.UUID(product_id), quantity
            )

    @function_tool
    async def view_cart(self, context: RunContext[CallState]) -> dict:
        """Read back what's currently in the caller's cart and the running total."""
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            return await commerce_main_agent.view_cart(cdb, ctx)

    @function_tool
    async def checkout(self, context: RunContext[CallState]) -> dict:
        """Turn the current cart into an order and run the policy check, once the caller
        is done adding items. Returns the exact total and whether the policy check
        passed — state the total plainly. If the policy check failed, or the result is a
        price_changed/out_of_stock error, explain it and do NOT call confirm_purchase.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            result = await commerce_main_agent.checkout(cdb, ctx)
        if result.get("ok"):
            context.userdata.mark_outcome("order_priced")
        return result

    @function_tool
    async def confirm_purchase(self, context: RunContext[CallState]) -> dict:
        """The explicit-approval step — ONLY call this after the caller has clearly said
        yes to the exact total checkout gave them (see CONFIRMATION in the system
        prompt). Never call this on an ambiguous response.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            return await commerce_main_agent.request_payment_confirmation_and_confirm(
                cdb, ctx
            )

    @function_tool
    async def start_payment(self, context: RunContext[CallState]) -> dict:
        """Prepare payment for a just-confirmed order. Never collects card numbers, UPI
        PINs, OTPs, or any payment credential — relay exactly what the result's
        "message_for_caller" explains about how they'll actually complete payment.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            result = await commerce_main_agent.initiate_payment(cdb, ctx)
        if result.get("ok"):
            result["message_for_caller"] = (
                f"Your order {result.get('razorpay_order_id', '')} is ready for payment. "
                "Please complete payment through the Bazaar Mitra app or website using "
                "your order reference — payment details are never collected over a call."
            )
            context.userdata.mark_outcome("payment_initiated")
        return result

    @function_tool
    async def transfer_to_payments_specialist(
        self, context: RunContext[CallState], reason: str
    ) -> tuple[Agent, str]:
        """Transfer to the Payments Specialist — for a failed payment, checking payment
        status, or retrying a payment. Not for starting a new purchase.

        Args:
            reason: Short reason for the handoff, e.g. "payment failed, wants to retry".
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            try:
                await commerce_handoff_service.initiate_handoff(
                    cdb, uuid.UUID(session_id), to_agent="payments_agent", reason=reason
                )
                await cdb.commit()
            except commerce_handoff_service.HandoffFailedError:
                await cdb.commit()
                return self, (
                    "I couldn't connect you to the payments specialist right now, but I can "
                    "still check what I have on this order — what would you like to know?"
                )
        specialist = PaymentsSpecialist(
            chat_ctx=self.chat_ctx.copy(exclude_instructions=True)
        )
        return specialist, "Let me connect you with our payments specialist."

    @function_tool
    async def transfer_to_order_support_specialist(
        self, context: RunContext[CallState], reason: str
    ) -> tuple[Agent, str]:
        """Transfer to the Order Support Specialist — for order status or cancelling an
        order before it's been paid for. Not for a failed payment (use
        transfer_to_payments_specialist) or returning something already received (use
        transfer_to_returns_specialist).

        Args:
            reason: Short reason for the handoff, e.g. "wants to cancel their order".
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            try:
                await commerce_handoff_service.initiate_handoff(
                    cdb,
                    uuid.UUID(session_id),
                    to_agent="order_support_agent",
                    reason=reason,
                )
                await cdb.commit()
            except commerce_handoff_service.HandoffFailedError:
                await cdb.commit()
                return self, (
                    "I couldn't connect you to order support right now, but I can still "
                    "check what I have — what would you like to know?"
                )
        specialist = OrderSupportSpecialist(
            chat_ctx=self.chat_ctx.copy(exclude_instructions=True)
        )
        return specialist, "Let me connect you with order support."

    # To add more tools, use the @function_tool decorator, following the pattern above.


class ReturnsSpecialist(SharedToolsMixin, Agent):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(instructions=RETURNS_SPECIALIST_PROMPT, chat_ctx=chat_ctx)

    async def on_enter(self) -> None:
        # Introduces itself after taking over, per Day 9 Step 5 — the caller shouldn't
        # have to guess that the conversation just changed hands.
        await self.session.generate_reply(
            instructions="""
            Introduce yourself, briefly, as Bazaar Mitra's Returns & Refunds Specialist —
            one short sentence — then ask what item they'd like to return, or what
            they'd like to know about the return policy. Don't re-ask anything the
            caller already told the previous agent; continue naturally from there.
            """
        )

    @function_tool
    async def check_return_eligibility(
        self,
        context: RunContext[CallState],
        item: str,
        days_since_purchase: int,
        opened_or_used: bool,
    ) -> dict:
        """Check whether an item is eligible for return under Bazaar Mitra's policy.

        Always call this before telling a caller whether their item can be returned —
        never guess. Ask the caller how many days since purchase, and whether the item
        has been opened/used, if you don't already know.

        Args:
            item: What the caller wants to return, e.g. "mustard oil", "wireless mouse".
            days_since_purchase: How many days ago they bought it.
            opened_or_used: Whether the item has been opened or used.
        """
        category = "general"
        lookup = await catalogue.lookup_products(item)
        if lookup.get("ok") and lookup.get("matches"):
            category = lookup["matches"][0].get("category", "general")

        result = returns_policy.check_eligibility(
            category, days_since_purchase, opened_or_used
        )
        context.userdata.mark_outcome("return_checked")
        return {
            **result,
            "category_used": category,
            "as_of": returns_policy.POLICY_LAST_UPDATED,
        }

    @function_tool
    async def initiate_return(
        self, context: RunContext[CallState], item: str, reason: str, eligible: bool
    ) -> dict:
        """Start a return request for an item, after confirming eligibility with
        check_return_eligibility. Only call this once you know whether it's eligible.

        Args:
            item: What's being returned.
            reason: Why the caller is returning it, in their own words.
            eligible: Whether check_return_eligibility said this item is eligible.
        """
        reference_id = await returns.create_return(
            caller_id=context.userdata.caller_id,
            item=item,
            reason=reason,
            eligible=eligible,
        )
        logger.info(
            f"Created return request {reference_id} for caller {context.userdata.caller_id}"
        )
        context.userdata.mark_outcome("return_initiated")
        return {"reference_id": reference_id, "eligible": eligible}

    @function_tool
    async def return_to_main_assistant(
        self, context: RunContext[CallState]
    ) -> tuple[Agent, str]:
        """Hand the conversation back to the main shopping assistant once the return
        request is handled or the caller asks about something outside returns/refunds.
        """
        assistant = Assistant(chat_ctx=self.chat_ctx.copy(exclude_instructions=True))
        return assistant, "I'll connect you back with the main assistant."


class PaymentsSpecialist(SharedToolsMixin, Agent):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(instructions=PAYMENTS_SPECIALIST_PROMPT, chat_ctx=chat_ctx)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="""
            Introduce yourself, briefly, as Bazaar Mitra's Payments Specialist — one
            short sentence — then say what you can see about their payment (call
            get_payment_status first if you haven't already) before asking how you can
            help. Don't re-ask anything the caller already told the previous agent.
            """
        )

    @function_tool
    async def get_payment_status(self, context: RunContext[CallState]) -> dict:
        """Check the current status of the payment for this session's order."""
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            return await commerce_payments_agent.get_payment_status(cdb, ctx)

    @function_tool
    async def get_payment_history(self, context: RunContext[CallState]) -> dict:
        """Show every payment attempt for this order, oldest first — use this when the
        caller wants to understand what happened (e.g. "why did it fail the first time"),
        not just the current status.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            return await commerce_payments_agent.get_payment_history(cdb, ctx)

    @function_tool
    async def retry_payment(self, context: RunContext[CallState]) -> dict:
        """Create a fresh payment attempt after a failure. Never marks any attempt
        successful itself — only the system's own verification does that, after the
        caller completes payment through the app/website.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            result = await commerce_payments_agent.retry_payment(cdb, ctx)
        if result.get("ok"):
            result["message_for_caller"] = (
                "A fresh payment attempt is ready. Please complete it through the Bazaar "
                "Mitra app or website using your order reference."
            )
        return result

    @function_tool
    async def return_to_main_assistant(
        self, context: RunContext[CallState]
    ) -> tuple[Agent, str]:
        """Hand the conversation back to the main shopping assistant once the payment
        issue is resolved or there's nothing more to do here.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            await commerce_payments_agent.return_to_main_agent(cdb, ctx)
        assistant = Assistant(chat_ctx=self.chat_ctx.copy(exclude_instructions=True))
        return assistant, "I'll connect you back with the main assistant."


class OrderSupportSpecialist(SharedToolsMixin, Agent):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(
            instructions=ORDER_SUPPORT_SPECIALIST_PROMPT, chat_ctx=chat_ctx
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="""
            Introduce yourself, briefly, as Bazaar Mitra's Order Support Specialist —
            one short sentence — then ask what they'd like to know about their order.
            Don't re-ask anything the caller already told the previous agent.
            """
        )

    @function_tool
    async def get_order_status(self, context: RunContext[CallState]) -> dict:
        """Check the current status of this session's order."""
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            return await commerce_order_support_agent.get_order_status(cdb, ctx)

    @function_tool
    async def cancel_order(self, context: RunContext[CallState], reason: str) -> dict:
        """Cancel the order — only works before any payment has gone through. If it
        can't be cancelled directly, explain why and suggest returns instead.

        Args:
            reason: Why the caller wants to cancel, in their own words.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            result = await commerce_order_support_agent.cancel_order(cdb, ctx, reason)
        if result.get("ok"):
            context.userdata.mark_outcome("order_cancelled")
        return result

    @function_tool
    async def get_fulfillment_status(self, context: RunContext[CallState]) -> dict:
        """General fulfillment status for the order — be upfront that detailed shipment
        tracking isn't available yet if the tool says so; never invent a delivery date.
        """
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            return await commerce_order_support_agent.get_fulfillment_status(cdb, ctx)

    @function_tool
    async def return_to_main_assistant(
        self, context: RunContext[CallState]
    ) -> tuple[Agent, str]:
        """Hand the conversation back to the main shopping assistant once done here."""
        session_id = await _ensure_commerce_session(context)
        async with commerce_db() as cdb:
            ctx = await commerce_context.load_context(cdb, uuid.UUID(session_id))
            await commerce_order_support_agent.return_to_main_agent(cdb, ctx)
        assistant = Assistant(chat_ctx=self.chat_ctx.copy(exclude_instructions=True))
        return assistant, "I'll connect you back with the main assistant."


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


def get_caller_id(ctx: JobContext) -> str:
    """
    Derive a stable id for the caller so we can look them up again on their next call.
    For SIP/telephony calls this prefers the caller's phone number (stable across calls
    from the same phone). Falls back to the participant identity, then the room name.

    NOTE: for web/browser test clients (e.g. a playground that assigns a random
    identity like "voice_assistant_user_1234" on every connection), this fallback is
    NOT stable across calls — that's expected, since there's no phone number to key
    off of. See the identify_caller tool for how the agent recovers a stable identity
    in that situation, by asking the caller directly.
    """
    for participant in ctx.room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone = participant.attributes.get(
                "sip.phoneNumber"
            ) or participant.attributes.get("sip.trunkPhoneNumber")
            if phone:
                return phone
        if participant.identity:
            return participant.identity
    # Last resort: no participant found yet, key off the room name.
    return ctx.room.name


def normalize_phone(phone_number: str) -> str:
    """Reduce a phone number to its last 10 digits, so the same number given with or
    without a country code across two calls (e.g. '+91 98765 43210' vs '9876543210')
    still resolves to the same caller record. Tuned for Indian 10-digit mobile numbers."""
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


async def _hangup_call() -> None:
    """Ends the call for everyone by deleting the current room."""
    ctx = get_job_context()
    if ctx is None:
        return
    await ctx.delete_room()


async def record_call_outcome(state: CallState, call_id: str, call_type: str) -> None:
    """
    Day 8: called once per call, when it ends, to record whether it reached the
    track's success condition — see CallState.mark_outcome() and every tool that calls
    it. Reads from session.userdata rather than any specific Agent instance, so this is
    correct even if a Day 9 handoff happened partway through the call.
    """
    outcome = "success" if state.outcome_achieved else "failed"
    reason = state.outcome_reason or "no_resolution"
    await call_stats.record_call(
        call_id=call_id,
        caller_id=state.caller_id,
        call_type=call_type,
        outcome=outcome,
        reason=reason,
        started_at=state.started_at.isoformat(),
    )
    logger.info(
        f"Recorded call outcome: {call_id} ({call_type}) -> {outcome} ({reason})"
    )


def build_outbound_opening(
    reason: Optional[str], call_context: dict, caller_record: Optional[dict]
) -> str:
    """
    Build the instructions for the very first thing the agent says on an OUTBOUND call.
    Encodes the Day 6 rule: within the first two sentences, say who's calling, why, and
    how to make it stop — before anything else, and without waiting for the caller to
    speak first.
    """
    name = (caller_record or {}).get("name")
    name_clause = f" ({name})" if name else ""
    shop = call_context.get("shop", "your local shop")

    if reason == "order_confirmation":
        order_summary = call_context.get("order_summary", "a recent order")
        return f"""
        This is an OUTBOUND call you initiated — the person did not call you and doesn't
        know who you are yet, so open carefully. Do NOT wait for them to speak first.

        Within your very first two sentences, before anything else, say:
        1. That this is Bazaar Mitra, calling on behalf of {shop}.
        2. Why you're calling: to confirm their order — {order_summary}.
        3. That they can ask you to stop calling at any time and you will (via
           opt_out_of_calls).

        Example shape (adapt naturally to the caller's likely language{name_clause}):
        "Namaste, this is Bazaar Mitra calling on behalf of {shop} about your order —
        {order_summary}. If you'd like me to stop calling, just say so anytime. Do you
        have a moment to confirm this order?"

        After that opening, listen to their response. You still can't yourself declare
        the order placed — relay their answer; {shop} finalizes it on their end.
        """

    # Generic fallback for any other outbound reason value.
    reason_phrase = reason or "a shopping-related update"
    return f"""
    This is an OUTBOUND call you initiated — the person did not call you and doesn't know
    who you are yet, so open carefully. Do NOT wait for them to speak first.

    Within your very first two sentences, before anything else, say who is calling
    (Bazaar Mitra, on behalf of {shop}), why you're calling ({reason_phrase}), and that
    they can ask you to stop calling at any time and you will (via opt_out_of_calls).
    """


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Make sure the memory database exists before we need it.
    await db.init_db()
    await escalations.init_db()
    await call_stats.init_db()
    await returns.init_db()

    # Set up a voice AI pipeline using Murf Falcon, Gemini, Deepgram, and the LiveKit turn detector
    session = AgentSession[CallState](
        userdata=CallState(),
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=deepgram.STT(model="nova-3", language="multi"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        llm=google.LLM(
            model="gemini-3.5-flash",
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
            voice="Samar",
            locale="hi-IN",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
    )

    # To use a realtime model instead of a voice pipeline, use the following session setup instead.
    # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/))
    # 1. Install livekit-agents[openai]
    # 2. Set OPENAI_API_KEY in .env.local
    # 3. Add `from livekit.plugins import openai` to the top of this file
    # 4. Use the following session setup instead of the version above
    # session = AgentSession(
    #     llm=openai.realtime.RealtimeModel(voice="marin")
    # )

    # # Add a virtual avatar to the session, if desired
    # # For other providers, see https://docs.livekit.io/agents/models/avatar/
    # avatar = hedra.AvatarSession(
    #   avatar_id="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/hedra
    # )
    # # Start the avatar and wait for it to join
    # await avatar.start(session, room=ctx.room)

    # Join the room first, so we can see who's calling (inbound) or place our own
    # call (outbound) before building the Assistant and starting the session.
    await ctx.connect()

    room_options = room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=lambda params: (
                noise_cancellation.BVCTelephony()
                if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                else noise_cancellation.BVC()
            ),
        ),
    )

    # ---- Detect an agent-initiated OUTBOUND call ----
    # Outbound calls are triggered by dispatching this agent with a phone number (and
    # why we're calling) in the job metadata — see trigger_outbound_call.py. Inbound
    # (phone or web) dispatches never set this, so dial_info stays empty for them and
    # everything below is skipped in favor of the existing inbound flow.
    dial_info: dict = {}
    if ctx.job.metadata:
        try:
            dial_info = json.loads(ctx.job.metadata)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Could not parse job metadata as JSON: {ctx.job.metadata!r}"
            )

    phone_number = dial_info.get("phone_number")

    if phone_number:
        # ---------------- OUTBOUND CALL ----------------
        call_reason = dial_info.get("reason")
        call_context = dial_info.get("context") or {}

        caller_id = normalize_phone(phone_number)
        ctx.log_context_fields["caller_id"] = caller_id

        if not OUTBOUND_TRUNK_ID:
            logger.error(
                "LIVEKIT_OUTBOUND_TRUNK_ID is not set — cannot place outbound call"
            )
            ctx.shutdown()
            return

        caller_record = await db.get_user(caller_id)
        if (caller_record or {}).get("facts", {}).get("do_not_call"):
            logger.info(f"Skipping outbound call to {caller_id}: marked do-not-call")
            ctx.shutdown()
            return

        sip_participant_identity = phone_number
        try:
            # This call blocks (wait_until_answered=True) until the phone is actually
            # picked up, so we never start the session — and never speak — into a
            # still-ringing or unanswered call.
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=OUTBOUND_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=sip_participant_identity,
                    wait_until_answered=True,
                )
            )
            logger.info(f"Outbound call to {phone_number} answered")
        except api.SipCallError as e:
            # e.g. USER_REJECTED (486/603), USER_UNAVAILABLE (408/480), SIP_TRUNK_FAILURE (5xx)
            logger.warning(
                f"Outbound call to {phone_number} failed: {e.sip_status_code} {e.sip_status}"
            )
            ctx.shutdown()
            return

        participant = await ctx.wait_for_participant(identity=sip_participant_identity)

        session.userdata.caller_id = caller_id
        session.userdata.started_at = datetime.now(timezone.utc)
        ctx.add_shutdown_callback(
            lambda: record_call_outcome(
                session.userdata, call_id=ctx.room.name, call_type="outbound"
            )
        )

        await session.start(
            agent=Assistant(),
            room=ctx.room,
            participant=participant,
            room_options=room_options,
        )

        # Outbound calls speak first — the callee didn't ask for this call, so we open
        # with who/why/how-to-stop rather than waiting for them to say something.
        opening_instructions = build_outbound_opening(
            call_reason, call_context, caller_record
        )
        await session.generate_reply(instructions=opening_instructions)
        return

    # ---------------- INBOUND CALL (phone or web) ----------------
    caller_id = get_caller_id(ctx)
    ctx.log_context_fields["caller_id"] = caller_id

    # Look the caller up ourselves, in plain Python, BEFORE the first generate_reply.
    #
    # Why not just let the LLM call the lookup_caller_history tool as its first action?
    # generate_reply(instructions=...) does not add anything to the chat history, so at
    # the very start of a session there is no "user" turn in history yet. If the model's
    # first output is a tool call at that point, Gemini rejects the follow-up request with
    # a 400 ("function call turn comes immediately after a user turn or after a function
    # response turn") because a function-call turn appeared with nothing before it.
    # Doing the lookup here sidesteps that entirely and is also more reliable, since the
    # greeting no longer depends on the model remembering to call the tool.
    caller_record = await db.get_user(caller_id)

    session.userdata.caller_id = caller_id
    session.userdata.started_at = datetime.now(timezone.utc)
    ctx.add_shutdown_callback(
        lambda: record_call_outcome(
            session.userdata, call_id=ctx.room.name, call_type="inbound"
        )
    )

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_options,
    )

    known_name = caller_record.get("name") if caller_record else None

    if caller_record is None:
        greeting_instructions = """
        This caller has no record on file — you have never spoken with them before.
        Introduce yourself as Bazaar Mitra, explain briefly what you can help with, and
        ask for their name so you can address them personally during this call (this is
        just for the conversation — do not ask about saving it yet). Then ask how you
        can assist today.
        """
    elif not known_name:
        # We recognize this caller (facts on file) but never got a name from them.
        greeting_instructions = f"""
        This caller has spoken with you before, but you don't have a name on file for
        them. Here is what is on file — use it naturally, do not read it out as a list:
        - Language preference: {caller_record.get("language_preference") or "unknown"}
        - Facts: {caller_record.get("facts") or {}}
        - Last interaction: {caller_record.get("last_interaction") or "unknown"}

        Welcome them back, briefly refer to what you last discussed (e.g. their last
        order or preferred shop), ask for their name so you can address them personally,
        then ask how you can help today.
        """
    else:
        greeting_instructions = f"""
        This caller has spoken with you before. Here is what is on file for them —
        use it naturally, do not read it out as a list:
        - Name: {known_name}
        - Language preference: {caller_record.get("language_preference") or "unknown"}
        - Facts: {caller_record.get("facts") or {}}
        - Last interaction: {caller_record.get("last_interaction") or "unknown"}

        Welcome them back by name and briefly refer to what you last discussed (e.g.
        their last order or preferred shop), then ask how you can help today.
        """

    await session.generate_reply(instructions=greeting_instructions)


if __name__ == "__main__":
    cli.run_app(server)
