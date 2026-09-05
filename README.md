# Bazaar Mitra — Agentic Commerce Platform for Local Kirana Shops

Bazaar Mitra started as a voice shopping assistant for India's local kirana stores,
built over the [10 Days of Voice Agents — VoiceForBharat Edition](https://github.com/murf-ai/voice-for-bharat-challenge-2026)
challenge on the [Murf LiveKit Starter](https://github.com/murf-ai/murf-livekit-starter).
It has since grown into a full **agentic commerce platform**: AI buyers can discover
merchants, compare real prices, place and pay for real orders through Razorpay, recover
from payment failures, and hand off to specialist agents — while merchants get an AI
growth agent that finds revenue opportunities in their own sales data, gated behind
their explicit approval before anything reaches a customer.

For the original voice-agent build story — what broke, what I learned along the way —
see the [companion blog post](https://dev.to/aj_infinite_2208/i-built-a-voice-shopping-assistant-for-local-kirana-stores-in-10-days-heres-everything-that-broke-k50).
This README covers the platform as it stands today.

**Test-mode only.** Every payment flow here runs against Razorpay's Test Mode. Nothing
in this repository is production-ready as-is — see [Limitations](#limitations) and
[Toward production](#toward-production) at the end.

---

## Why this isn't "a chatbot with a payment button"

The thing that makes this an *agentic commerce platform* rather than a shopping
chatbot with a `pay()` call bolted on is where the money logic lives:

- **The LLM never decides whether a transaction is allowed.** A deterministic policy
  engine (`policy_service.py`) checks transaction/daily limits against the real
  database, in Python, before any payment is created — and again immediately before
  the payment actually fires, because the LLM could be wrong, forgetful, or
  manipulated, and the limit check can't be.
- **A verified signature is not the same as a paid order.** Razorpay Checkout returning
  a signed response only proves the response wasn't tampered with in transit — the
  *order* only becomes `PAID` once a verified webhook confirms it server-side. Two
  independent confirmations, never a client callback alone.
- **Every financial action is audited**, including the failures — a policy rejection,
  an out-of-stock block, a gateway timeout all write an explainable `audit_events` row,
  not just the successes.
- **Specialists are narrowly scoped and can fail safely.** A handoff to an unavailable
  or unknown agent doesn't strand the conversation; it explains and falls back.

The full safety model is in [Safety model](#safety-model) below.

---

## Architecture

```
┌─────────────┐   ┌──────────────┐   ┌───────────────────┐
│  Voice call  │   │  AI Buyer    │   │  Merchant          │
│ (LiveKit)    │   │  (/buyer)    │   │  Dashboard          │
└──────┬───────┘   └──────┬───────┘   │  (/merchant)        │
       │                  │           └──────┬─────────────┘
       │  in-process       │  HTTP            │  HTTP
       │  app.agents.*     │                  │
       ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI Commerce API (backend/app)       │
│  ┌────────────┐ ┌──────────────┐ ┌─────────────────┐  │
│  │ main_agent │ │ payments_    │ │ returns_agent    │  │
│  │            │ │ agent        │ │ order_support_   │  │
│  │            │ │ growth_agent │ │ agent            │  │
│  │            │ │ buyer_agent  │ │                  │  │
│  └─────┬──────┘ └──────┬───────┘ └────────┬─────────┘  │
│        │  handoff_service ties these together, backed  │
│        │  by agent_sessions/handoffs in Postgres        │
│        ▼                                                │
│  catalog / cart / order / policy / payment / audit /    │
│  recommendation / growth services                       │
└───────────────────────┬───────────────────────────────┘
                         │
                ┌────────▼────────┐        ┌──────────────┐
                │   PostgreSQL     │        │  Razorpay     │
                │  (single source  │◄──────►│  Test Mode    │
                │   of truth)      │        │  (Orders API, │
                └──────────────────┘        │   Checkout,   │
                                             │   Webhooks)   │
                                             └──────────────┘
```

Voice, the AI Buyer, and the merchant dashboard are three different **channels** onto
the exact same commerce engine — none of them has its own copy of the cart/order/
policy/payment logic. The voice worker (`backend/src/agent.py`) is a separate LiveKit
process that imports `app.agents.*`/`app.services.*` directly (in-process, sharing the
Postgres database) rather than making HTTP calls to itself; the AI Buyer and merchant
dashboard talk to the same logic over the FastAPI HTTP API instead, since they're
genuinely separate clients.

---

## The AI Buyer flow

```
"Find me a wireless mouse under ₹1,000 in stock"
        │
        ▼
Cross-merchant search (real prices/stock only, catalog_service)
        │
        ▼
Deterministic selection: cheapest available option
  — explains cheaper-but-unavailable alternatives too
        │
        ▼
Cart created, item added
        │
        ▼
Order created + policy check run — exact total shown
        │
        ▼
EXPLICIT confirmation required — never implied by
the original request, even one that said "...and buy it"
        │
        ▼
Razorpay order created (payment NOT yet complete)
        │
        ▼
Razorpay Checkout → payment attempt
        │
        ├─ fails → explained, order stays unpaid, retry offered
        │
        ▼
Signature verified server-side (necessary, not sufficient)
        │
        ▼
Webhook confirms capture → order marked PAID
        │
        ▼
Audit trail + merchant analytics update — for real, not mocked
```

Try it at `/buyer` once the stack is running. One deliberate design choice worth
knowing: `buyer_agent.select_best_available`'s product-selection logic is a
**deterministic rule** (cheapest in-stock match), not an LLM call — the same
"keep business logic out of prompts" principle applied to the one piece of buyer
"intelligence" that actually touches money.

## The merchant growth flow

```
Merchant: "How can I increase revenue?"
        │
        ▼
growth_service aggregates real revenue/product/recommendation
data — nothing estimated, nothing cached
        │
        ▼
Cross-sell opportunities ranked by ACTUAL converted revenue
(recommendation_service tracks every suggestion: shown → accepted → converted)
        │
        ▼
Growth agent drafts a campaign (always PENDING_APPROVAL)
        │
        ▼
Merchant explicitly approves — nothing runs without this
        │
        ▼
Campaign tracked (targeted/sent/converted) — see the note
below on what "execute" actually does
```

Try it at `/merchant` → AI Growth tab.

**Scope note:** campaign "execution" records the targeting/tracking pipeline (who was
targeted, marked "sent") — it does not call any real SMS/email/push provider. No
messaging credentials exist in this project's env vars, and actual message delivery is
outside this build's scope; only the approval-gated decisioning and its conversion
tracking are implemented.

## The Razorpay payment flow

See the diagram in [Why this isn't "a chatbot with a payment button"](#why-this-isnt-a-chatbot-with-a-payment-button)
for the two-confirmation model. Mechanically:

1. Backend creates a Razorpay **Order** (`POST /v1/orders` via the official SDK) —
   amount is always converted to paise server-side, never trusted from the client.
2. Frontend opens Razorpay **Checkout** with that order id.
3. On completion, the frontend calls `POST /api/v1/payments/verify` with the
   `razorpay_payment_id`/`razorpay_signature` Checkout returned. The backend
   recomputes `hmac_sha256(order_id|payment_id, key_secret)` itself and compares —
   this is pure cryptography against a secret only the backend holds, no network call
   to Razorpay needed, which is why this exact path is fully tested even in an
   environment with no internet access to Razorpay (see [Limitations](#limitations)).
4. The **webhook** (`POST /api/v1/webhooks/razorpay`) is the actual source of truth —
   its signature is verified against the *raw* request body (re-serializing JSON before
   verifying is a documented pitfall) before anything in the payload is trusted.
5. Every attempt is its own row in `payments`, never overwritten — a failed attempt
   followed by a successful retry leaves both in the history.

---

## Specialist agents

| Agent | Handles | Can never |
|---|---|---|
| **Main Commerce Agent** | Search, cart, checkout, routing | Bypass policy, claim payment success, invent price/stock |
| **Payments Specialist** | Status, failure explanation, retry | Mark a payment successful itself |
| **Returns & Refunds Specialist** | Eligibility (real DB state), return requests | Approve a refund the DB state doesn't support |
| **Order Support Specialist** | Status, cancellation (pre-payment only) | Cancel an already-paid order directly (routes to returns) |
| **Merchant Growth Agent** | Revenue/product analytics, campaign drafts | Execute a campaign without merchant approval |
| **AI Buyer** | Cross-merchant discovery + the same checkout flow everyone else uses | Skip explicit confirmation, bypass policy |

Handoffs carry context automatically (`handoff_service`, backed by
`agent_sessions`/`handoffs`) — the caller never repeats themselves. A handoff to an
agent that isn't reachable (unknown name, or a merchant-only agent from a buyer
session) fails cleanly and explains itself rather than leaving the conversation stuck.

---

## Safety model

The exact chain every consequential action goes through:

```
LLM decides WHAT it wants to do
       ↓
Tool validates WHAT is allowed (narrow, typed, service-backed)
       ↓
Policy engine decides WHETHER it is allowed (deterministic, DB-backed)
       ↓
User confirms sensitive action (explicit, never implied)
       ↓
Backend performs the financial operation
       ↓
External system (Razorpay) responds
       ↓
Backend verifies (signature) AND reconciles (webhook)
       ↓
Database records the final state
       ↓
Audit event emitted
       ↓
Agent explains the result
```

The LLM is never one step away from an external payment API — every hop in that chain
is real, typed, testable Python.

## Audit trail

Every order confirm/reject, every policy check, every payment create/verify, every
webhook event, and every return request writes an `audit_events` row via
`audit_service` — actor, amount, policy result, confirmation state, success/failure
reason, all without ever storing a secret. Browse it at `/merchant` → Audit, or
`GET /api/v1/audit/{resource_id}` for a single order's full story.

One subtlety worth knowing if you touch this code: a few call sites commit the audit
write *immediately*, before raising the exception the route will catch-and-rollback on
— otherwise the rollback would silently discard the very record explaining why an
action was blocked. See the comments in `order_service.py`/`payment_service.py` at
each such spot.

## Failure recovery

| Failure | What happens |
|---|---|
| Price changed after being quoted | Order creation blocked (409) until the buyer sees the new total and explicitly re-confirms |
| Item out of stock | Always blocked, no override — nothing to charge for |
| Payment gateway unreachable | No phantom payment row written; order stays exactly as it was, ready to retry |
| Payment declined | Marked `FAILED`, order stays unpaid; retry creates a fresh attempt, never overwrites the failed one |
| Invalid payment signature | Marked `FAILED` immediately — never silently accepted |
| Duplicate payment request | Idempotency key returns the existing attempt instead of creating a second Razorpay order |
| Repeated webhook delivery | Detected and no-op'd, never double-processed |
| Specialist unavailable | Handoff fails cleanly, main agent explains and keeps helping |

---

## Database architecture

PostgreSQL, SQLAlchemy 2.0 async, Alembic migrations. 18 tables — see
`backend/app/db/models/`. A few decisions worth knowing:

- Every model has a UUID primary key with **both** a Python-side default and a
  Postgres `server_default=gen_random_uuid()`, so raw-SQL seed/debug inserts get a
  valid id too.
- `payments` merges the spec's `payments`/`payment_attempts` split into one table
  (one row per attempt, never overwritten) — see the `Payment` model docstring for why.
- `audit_events` has no foreign keys to what it references — an audit row must outlive
  the entity it describes.
- Cart items persist the price quoted at add-time (`unit_price`), independent of the
  live product price — order creation re-validates this and blocks silent drift.

Run migrations / seed data:

```bash
cd backend
uv run alembic upgrade head
uv run python -m app.seed   # idempotent — 5 merchants, ~26 products, historical orders
```

## API documentation

FastAPI auto-generates OpenAPI docs at `http://localhost:8000/docs` once the backend
is running. A machine-readable capability summary for other AI agents is at
`GET /api/v1/agent/capabilities`.

---

## Setup

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and [pnpm](https://pnpm.io/) (`corepack enable && corepack prepare pnpm@latest --activate`)
- PostgreSQL 16 (or use Docker Compose, below)
- A [LiveKit Cloud](https://cloud.livekit.io/) project (for the voice channel)
- [Deepgram](https://deepgram.com), [Murf](https://murf.ai/api/dashboard), and
  [Google AI Studio](https://aistudio.google.com/apikey) API keys (for the voice channel)
- A [Razorpay](https://dashboard.razorpay.com/signup) account in **Test Mode** (for payments)

### Backend

```bash
cd backend
cp .env.example .env.local     # fill in the values below
uv sync
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --port 8000
```

Voice worker (separate process, same `.env.local`):

```bash
cd backend
uv run src/agent.py start
```

### Frontend

```bash
cd frontend
cp .env.example .env.local
pnpm install
pnpm dev
```

Visit `http://localhost:3000` for the voice UI, `/buyer` for the AI Buyer, `/merchant`
for the dashboard.

### Docker Compose (backend + Postgres + frontend in one command)

```bash
docker compose up --build
```

The voice worker needs LiveKit/Deepgram/Murf/Google credentials it may not have in a
quick eval, so it's an opt-in profile:

```bash
docker compose --profile voice up --build
```

## Environment variables

See `backend/.env.example` and `frontend/.env.example` for the full annotated list.
The essentials:

```bash
# backend/.env.local
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/bazaar_mitra
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
MAX_TRANSACTION_AMOUNT=5000
DAILY_TRANSACTION_LIMIT=10000
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
DEEPGRAM_API_KEY=...
MURF_API_KEY=...
GOOGLE_API_KEY=...

# frontend/.env.local
NEXT_PUBLIC_COMMERCE_API_URL=http://localhost:8000
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
```

## Test-mode payment setup

1. Sign up at [dashboard.razorpay.com](https://dashboard.razorpay.com/signup) — no
   business documents needed for Test Mode.
2. Switch the dashboard to **Test Mode** (toggle near your profile icon).
3. **Account & Settings → API Keys → Generate Key** (while in Test Mode) — save the
   Key Id and Key Secret immediately, the secret is shown once.
4. Paste both into `backend/.env.local`.
5. For webhooks: **Settings → Webhooks → Add New Webhook**, pointed at
   `POST /api/v1/webhooks/razorpay` behind a tunnel (e.g. [ngrok](https://ngrok.com))
   for local dev, subscribed to `payment.captured`, `payment.failed`, `order.paid`, with
   a webhook secret matching `RAZORPAY_WEBHOOK_SECRET`.

Razorpay's [test card/UPI numbers](https://razorpay.com/docs/payments/payments/test-card-upi-details/)
let you trigger both successful and failed payments on demand.

## Demo instructions

1. `docker compose up --build` (or run backend + frontend separately, above).
2. Open `/buyer`. Enter any phone number. Ask for *"wireless mouse under ₹1000 that is
   in stock"* — this reproduces the seeded three-merchant comparison (₹799 in stock,
   ₹899 in stock, ₹699 out of stock) and picks the ₹799 option, explaining why.
3. Confirm, then pay (opens real Razorpay Checkout if keys are configured; otherwise
   shows what would happen).
4. Open `/merchant`, pick **Gupta Electronics**, and watch Overview/Orders update with
   the order you just placed.
5. Go to **AI Growth** — after a cross-sell has been accepted and converted at least
   once, an opportunity appears with a "Draft a cross-sell campaign" button; approve it.
6. Check **Audit** for the full explainable trail of everything above.
7. For the voice channel: run the LiveKit worker and connect via the `/` voice UI or a
   phone number — ask to buy something, then ask "what's my payment status" to see the
   Payments Specialist handoff, or "I want to return this" for Returns.

## Example conversations

**AI Buyer (text, `/buyer`):**
> "Find me a wireless mouse under ₹1,000 that is in stock"
> → *"Wireless Mouse from Gupta Electronics at ₹799 (23 in stock) is the best available
> option. Verma Mobile & Accessories is cheaper at ₹699 but currently out of stock."*
> → Order total ₹799, policy PASS → confirm → pay.

**Voice (Hinglish):**
> Caller: *"Mujhe ek wireless mouse chahiye ₹1000 ke andar."*
> Agent: *"₹799 ka mouse available hai, Gupta Electronics mein."*
> Agent: *"Iske saath ₹199 ka mouse pad bhi commonly liya jata hai — add karna chahenge?"*
> Caller: *"Haan, add kar do."*
> Agent: *"Total ₹998 hai. ₹1,000 ki transaction limit ke andar hai. Payment ke liye
> aapki confirmation chahiye — proceed karun?"*
> Caller: *"Haan."*
> Agent creates the order, confirms, and starts payment — never asking for card/UPI
> details over the call.

**Merchant (dashboard):**
> Growth tab shows: *"Customers buying Wireless Mouse frequently accept Mouse Pad — 6
> accepted, 4 converted, ₹796 generated so far."* → merchant clicks **Draft a cross-sell
> campaign** → reviews → approves.

---

## Limitations

- **Test Mode only.** No production Razorpay integration; no PCI-DSS review; not
  audited for real-money use.
- **No real message delivery.** Campaign "execution" tracks targeting/sends but never
  calls an SMS/email/push provider — no such credentials are part of this project.
- **Voice payment completion is out-of-band.** The voice agent never collects card/UPI
  details (by design) and currently tells the caller to complete payment via the
  web app using their order reference, rather than sending an SMS/WhatsApp link —
  there's no messaging integration to send one.
- **This was built in a sandboxed environment with no network access to
  `api.razorpay.com`, LiveKit, Deepgram, or Murf.** Every code path that needs those
  services is written against their official, current documentation and covered by
  tests that mock the external network call — but the actual credentialed round trip
  (`razorpay.Client(...).order.create(...)`, a live LiveKit voice session) has not been
  exercised end-to-end by the people who wrote this code. Signature verification
  (payment and webhook) needed *zero* network access to test for real, since it's pure
  HMAC against a secret the backend already holds — that part is fully verified live.
  Exercise the credentialed paths first in your own environment.
- **Single global return-window policy**, not per-merchant.
- **No fulfillment/shipment tracking** — order-support's delivery status is an honest
  proxy off order state, not real courier data.
- **No authentication on the merchant dashboard.** Anyone with the URL can view/act as
  any merchant — fine for a demo, not for anything real.
- **AI Buyer's NL query parsing is deterministic regex**, not an LLM — handles the
  price/stock phrasing patterns it's built for (including common Hinglish forms), not
  arbitrary phrasing.

## Toward production

- Real Razorpay Live Mode + a PCI-DSS review of anything payment-adjacent.
- Authentication/authorization on every merchant-scoped endpoint (today, `merchant_id`
  is a plain query/body parameter — nothing stops one merchant's dashboard session from
  querying another's data by changing an id in the URL).
- A message-delivery integration (SMS/WhatsApp/email) with opt-out handling for
  campaigns to actually reach anyone.
- Real fulfillment/shipment tracking.
- Rate limiting and request validation hardening on public endpoints.
- Move the voice worker's in-process `app.*` imports to a proper internal API/queue
  boundary if it and the FastAPI app ever need to scale or deploy independently.
- Replace the deterministic NL search parser with a hybrid LLM-assisted one for
  genuinely open-ended buyer phrasing, while keeping the underlying filters/selection
  deterministic.

---

## Repository layout

See [`AGENTS.md`](./AGENTS.md) for the full file-by-file map, dev commands, and
implementation notes (including a couple of real bugs the test suite caught along the
way and how they were fixed) — this README is the "what and why," AGENTS.md is the
"where and how."
