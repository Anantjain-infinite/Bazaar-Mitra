# AGENTS.md

This is a monorepo for a voice AI agent starter, powered by Murf Falcon TTS and LiveKit Agents.

## Repository structure

```
murf-livekit-starter/
├── backend/
│   ├── src/agent.py    # Voice agent entrypoint (LiveKit Agents + Murf Falcon TTS)
│   ├── app/            # Commerce API (FastAPI + PostgreSQL) — see "Commerce Backend" below
│   ├── migrations/      # Alembic migrations for app/db/models
│   └── tests/           # LLM-judged eval tests for the voice agent
├── frontend/         # Next.js UI (LiveKit Agents UI components + AI Buyer / merchant dashboards)
│   ├── app/          # Pages and API routes
│   ├── components/   # UI components (agents-ui, app, ui)
│   └── app-config.ts # Branding and feature config
├── docker-compose.yml # postgres + commerce API + frontend (voice worker is an opt-in profile)
├── start_app.sh      # Start all services (macOS/Linux)
└── start_app.ps1     # Start all services (Windows)
```

## Backend

### Tech stack
- **Python 3.10+** with **uv** package manager
- **LiveKit Agents SDK** (`livekit-agents ~1.4`) — voice AI agent framework
- **Murf Falcon** (`livekit-murf`) — text-to-speech
- **Deepgram Nova-3** — speech-to-text
- **Google Gemini** — LLM
- **Silero VAD** + **LiveKit Turn Detector** — voice activity and turn detection

### Key file: `backend/src/agent.py`
This is the single entrypoint. It contains:
- `SYSTEM_PROMPT` — controls the agent's behavior (change this to change the use case)
- `Assistant` class — extends `Agent`, where tools are added via `@function_tool`
- `my_agent()` — sets up the voice pipeline (STT → LLM → TTS) and connects to LiveKit
- `prewarm()` — pre-loads the Silero VAD model

### Running the backend
```bash
cd backend
uv sync
uv run python src/agent.py download-files   # first time only
uv run python src/agent.py dev              # development
uv run python src/agent.py console          # terminal-only testing
```

### Environment variables
Copy `backend/.env.example` to `backend/.env.local`. Required keys:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `MURF_API_KEY`
- `DEEPGRAM_API_KEY`
- `GOOGLE_API_KEY`

### Code style
Uses **ruff** for linting and formatting:
```bash
uv run ruff check .
uv run ruff format .
```
Config is in `pyproject.toml` — 88 char line length, double quotes, space indent.

### Testing
Tests are in `backend/tests/test_agent.py`. They use LiveKit's testing framework with LLM-as-judge evaluation (not mocks). Run with:
```bash
uv run pytest
```
Requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` to be set.

When modifying the system prompt or adding tools, write tests first. Use the existing tests as a pattern — they call `session.run(user_input=...)` and use `.judge()` to evaluate responses.

### Dependencies
Managed via `uv` and defined in `pyproject.toml`. Always use `uv sync` and `uv run` — never `pip install`.

## Commerce Backend (`backend/app/`)

This is the new agentic-commerce layer being built alongside the voice agent —
catalog, cart, orders, Razorpay payments, policy engine, and audit trail, all
exposed as a documented REST API that both the voice agent's tools and the
separate AI Buyer client call into. It lives in the **same `backend/`
directory and the same `uv` environment** as `src/agent.py` — one
`pyproject.toml`, one `.venv`, one `.env.local`.

### Tech stack
- **FastAPI** + **Uvicorn** — the commerce API
- **PostgreSQL** + **SQLAlchemy 2.0 (async)** — persistence (see `app/db/models/`)
- **Alembic** — migrations (see `migrations/`)
- **Razorpay Python SDK** — Test Mode payments (see `app/integrations/razorpay.py`, added in the payments phase)

### Structure
```
backend/app/
├── main.py            # FastAPI app, health/ready, router registration
├── config.py           # Settings (env vars) — single source of truth for DATABASE_URL etc.
├── seed.py              # `python -m app.seed` — idempotent demo data (merchants/products/orders)
├── api/routes/         # One file per resource (merchants, catalog, carts, orders, ...)
│   ├── merchants.py     # GET /api/v1/merchants[/{id}]
│   ├── catalog.py       # GET /api/v1/merchants/{id}/catalog|products|search — per-merchant reads
│   ├── agent.py          # GET /api/v1/agent/catalog, POST /api/v1/agent/search — cross-merchant, agent-facing
│   ├── carts.py           # POST /api/v1/carts, item add/update/remove
│   ├── orders.py           # POST /api/v1/orders (from cart), GET, POST /{id}/confirm
│   ├── payments.py          # POST /api/v1/payments/create|{id}/retry|verify, GET /{id}
│   ├── webhooks.py           # POST /api/v1/webhooks/razorpay (raw-body signature verification)
│   ├── audit.py                # GET /api/v1/audit, GET /api/v1/audit/{resource_id}
│   ├── analytics.py             # GET /api/v1/merchant/analytics|revenue|recommendations
│   └── growth.py                 # POST/GET /api/v1/growth/campaigns + approve/reject/execute/metrics
├── agents/             # main_agent, payments_agent, returns_agent, growth_agent, buyer_agent
├── services/            # Business logic — policy_service, payment_service, audit_service, ...
│   ├── policy_service.py  # Deterministic transaction-limit enforcement — see spec section 18
│   ├── cart_service.py     # Add/update/remove cart items; always quotes price at add-time
│   ├── order_service.py     # Cart -> Order (price/stock integrity checks) and the confirm gate
│   ├── payment_service.py    # Razorpay order creation, signature verification, webhook reconciliation
│   ├── audit_service.py       # The append-only audit_events log every consequential action writes to
│   ├── session_service.py      # AgentSession CRUD — the durable backing store for AgentContext
│   ├── handoff_service.py       # Specialist handoff protocol, incl. the handoff-failure path
│   ├── buyer_service.py          # get-or-create bridge (e.g. voice caller phone -> Buyer row)
│   ├── returns_service.py         # DB-backed return eligibility/requests (replaces returns_policy.py)
│   ├── recommendation_service.py   # Upsell/cross-sell lifecycle: shown -> accepted -> converted
│   └── growth_service.py            # Merchant revenue/product analytics + the campaign approval pipeline
├── agents/               # Tool-layer functions each specialist agent exposes (framework-agnostic —
│   │                       callable from voice, a future AI Buyer, or tests, with no LLM involved)
│   ├── context.py          # AgentContext (spec section 13) + load_context/save_context
│   ├── main_agent.py        # search/cart/checkout/confirm/pay + handoff_to_* tools
│   ├── payments_agent.py     # payment status/history/retry
│   ├── returns_agent.py       # return policy/eligibility/request/refund status
│   ├── order_support_agent.py  # order status/cancel/fulfillment
│   ├── growth_agent.py          # merchant-facing revenue/product/campaign tools
│   └── buyer_agent.py            # AI Buyer: cross-merchant discover_and_compare + buy_best_available
├── integrations/
│   └── razorpay.py          # Order creation (official SDK) + both signature-verification formulas
├── db/
│   ├── models/          # SQLAlchemy models (import from app.db.models to get everything)
│   ├── repositories/     # DB access patterns, where needed beyond simple service+session use
│   └── session.py        # Async engine/session factory + `get_db` FastAPI dependency
├── integrations/         # razorpay.py, livekit.py — external API clients
└── tests/                 # pytest suite for this package (separate from backend/tests/)
```

### Running the commerce backend
```bash
cd backend
uv sync                                    # picks up FastAPI/SQLAlchemy/Alembic too — one lockfile
uv run alembic upgrade head                # apply migrations (needs DATABASE_URL reachable)
uv run uvicorn app.main:app --reload --port 8000
```
Or via Docker (brings up Postgres too): `docker compose up --build` from the repo root.

API docs once running: `http://localhost:8000/docs` (OpenAPI/Swagger).

### Database
- `DATABASE_URL` in `.env.local` — see `backend/.env.example`. Uses the `postgresql+asyncpg://` driver for the app and `postgresql+psycopg2://` for Alembic (derived automatically, don't set both by hand).
- New model → new migration:
  ```bash
  uv run alembic revision --autogenerate -m "describe the change"
  uv run alembic upgrade head
  ```
  Always read the generated migration before applying it — autogenerate doesn't catch everything (e.g. renames look like drop+add).
- Seed realistic multi-merchant demo data (5 merchants, ~26 products, cross-sell/upsell relationships, transaction policies, a few historical orders/payments) — idempotent, safe to re-run:
  ```bash
  uv run python -m app.seed
  ```
- Every model uses a UUID primary key with both a Python-side default (`uuid.uuid4`) and a Postgres-side `server_default=gen_random_uuid()`, so raw-SQL seed/debug inserts also get a valid id.

### Testing
`backend/app/tests/` (this package) is separate from `backend/tests/` (the voice agent's LLM-judged evals) and runs against a real Postgres test database, not SQLite or mocks:
```bash
createdb bazaar_mitra_test          # one-time, or docker exec into the postgres container
uv run pytest app/tests -v
```
Override the test DB with `TEST_DATABASE_URL` if needed. Fixtures live in `app/tests/conftest.py` — `db_session` gives a real session against a schema created fresh per test and dropped after; `client` gives an `httpx.AsyncClient` wired to the FastAPI app with `get_db` overridden to reuse that same session.

### Payments
The payment state machine deliberately requires TWO independent server-side confirmations before an order becomes `PAID` — see the `payment_service.py` module docstring:
1. `POST /api/v1/payments/verify` checks the Checkout signature (`hmac_sha256(order_id|payment_id, key_secret)`) — this only proves the response wasn't tampered with in transit, so it moves the *Payment* row to `AUTHORIZED`, not the *Order* to `PAID`.
2. Only a verified `payment.captured` webhook (`hmac_sha256(raw_body, webhook_secret)`) moves the *Order* to `PAID`.

Every payment attempt is its own `Payment` row (never overwritten), ordered by `attempt_number` — this is a deliberate simplification of the spec's separate `payments`/`payment_attempts` tables; see the `Payment` model docstring.

**Sandbox limitation this was built under:** this environment cannot reach `api.razorpay.com`, so `razorpay_integration.create_order`'s real network call has never been exercised here — it's covered by tests that mock the SDK client. Both signature-verification functions, however, are pure HMAC against a secret you already hold and need zero network access — those are tested for real, including live end-to-end runs against a locally-running server during development. Exercising `create_order` against real Razorpay test-mode credentials is the one thing you'll want to do first in your own environment.

### Audit trail
Every consequential action (order confirm, policy pass/fail, payment create/verify, webhook processing, return requests) writes an `audit_events` row via `audit_service`. One subtlety worth knowing if you touch this code: several call sites (policy-rejected payment, out-of-stock order block) commit the audit write immediately, *before* raising the exception the route handler will catch-and-rollback on — otherwise the route's `db.rollback()` would silently discard the very audit record explaining *why* the action was blocked. See the comments at each such call site in `order_service.py`/`payment_service.py`.

### Agent architecture
`app/agents/` holds the tool-layer functions for the Main Commerce Agent and three specialists (Payments, Returns, Order Support) — see spec sections 9-13. Each function takes `(db, ctx: AgentContext, ...)` and returns a plain dict; nothing here depends on an LLM or a voice framework, so the whole multi-agent flow (search → cart → checkout → confirm → pay → handoff to a specialist → handoff back) is tested directly in `app/tests/test_phase5_agents.py` without any live model or voice call.

`AgentContext` (`app/agents/context.py`) is always loaded from and saved back to the `agent_sessions` table (`session_service`) — never held only on a Python object — because a specialist handoff creates a brand-new agent instance with no memory of `self`. This carries forward the same design principle the existing voice agent's `CallState` already uses.

`handoff_service.initiate_handoff` fails cleanly (`HandoffFailedError`) for an unknown or currently-unreachable specialist rather than leaving a session stuck — see spec section 12. `growth_agent` is a deliberate example: it's real (Phase 8) but merchant-facing, so a buyer-side session handing off to it hits this same failure path today.

**Voice agent integration — done.** `backend/src/agent.py`'s `Assistant` gained real commerce tools (`find_and_compare_products`, `add_item_to_cart`, `view_cart`, `checkout`, `confirm_purchase`, `start_payment`) that call `app.agents.buyer_agent`/`main_agent` directly, plus two new specialist `Agent` subclasses (`PaymentsSpecialist`, `OrderSupportSpecialist`) alongside the existing `ReturnsSpecialist`. Since this is a separate LiveKit worker process (not a FastAPI request handler), it imports `app.*` in-process via an explicit `sys.path` bridge at the top of the file (`uv run src/agent.py` only puts `src/` on the path by default, not its parent) — every commerce import is module-qualified (`import app.agents.main_agent as commerce_main_agent`, never a bare `from app.db.models import AgentSession`) specifically because this file already uses **LiveKit's own** `AgentSession` class throughout; a bare import would silently shadow it. `CallState` gained exactly one new field, `commerce_session_id` — everything else (cart/order/payment ids, current specialist) lives in the `agent_sessions` DB row that id points to, not on `CallState`, for the same "a handoff creates a new agent instance" reason `AgentContext` exists at all. A caller's phone number is lazily bridged to a real `Buyer` row (`_ensure_commerce_session`) the first time they do anything commerce-related in a call.

Payment over voice never collects card/UPI/OTP details — `start_payment` creates the Razorpay order server-side and tells the caller to complete it via the app/web dashboard using their order reference; see the README's Limitations section for why (no SMS/messaging integration to send a link instead).

**How this was verified without live LiveKit/Deepgram/Murf credentials:** the module was fully imported (proving the sys.path bridge and every new import resolve correctly), then every new tool's underlying coroutine was called directly against a real Postgres database with a lightweight fake `RunContext`-like object (bypassing only the LiveKit session machinery those tools don't actually need) — this exercised the complete real flow end-to-end: search → cart → checkout → policy check → confirm → payment creation, plus a real DB-level handoff to `PaymentsSpecialist` and its tools correctly reporting "no payment yet" rather than fabricating state. One real bug surfaced this way and was fixed: `find_and_compare_products` (via `discover_and_compare`) doesn't lock in a merchant, since the caller might pick any product from the comparison — `add_item_to_cart` now looks up the chosen product's merchant and calls `select_merchant` first if needed, rather than assuming one was already set.

### AI Buyer, recommendations, and growth (Phases 6-8)

**AI Buyer** (`app/agents/buyer_agent.py`) reuses `main_agent`'s cart/checkout/confirm/pay tools rather than duplicating business logic — see spec section 26. Its one buyer-specific piece, `discover_and_compare`, searches across ALL merchants and selects the cheapest in-stock match. This selection is a **deterministic rule, not an LLM call** — a deliberate choice, not a shortcut: it's the same "keep business logic out of prompts" principle (spec section 4) applied to the one piece of buyer intelligence that touches money. Exposed over HTTP as session-aware endpoints (`POST /api/v1/agent/sessions`, then `/cart`, `/checkout`, `/confirm`, `/pay`, `/buy-best-available` — all scoped by `?session_id=`), so any external AI agent can drive the whole flow, not just the in-process voice worker. `catalog_service.natural_language_search` gained an `include_out_of_stock` flag specifically for this — a query that says "in stock" would otherwise filter out-of-stock alternatives out of the SQL entirely, leaving `discover_and_compare` nothing to explain ("Merchant C was cheaper but unavailable") even though that comparison is the point.

**Recommendation tracking** (`app/services/recommendation_service.py`) follows every suggestion through shown → accepted → converted. `main_agent.get_recommendations` records "shown" and caches pending recommendation ids on the session; `main_agent.add_to_cart` checks that cache and marks an acceptance when the buyer actually adds a recommended product; `payment_service`'s webhook handler calls `mark_converted_for_order` the moment an order becomes `PAID`, setting `revenue_impact` from the real order line item — never an estimate.

**Growth agent** (`app/agents/growth_agent.py`, `app/services/growth_service.py`) is merchant-facing only — every tool requires `ctx.merchant_id`. Campaigns always start `PENDING_APPROVAL`; `execute_campaign` refuses to run an unapproved campaign (`ValueError`), matching spec section 23's "never let an LLM spam customers autonomously." **Scope note:** `execute_campaign` records the targeting/tracking pipeline (who was targeted, marked "sent") but does not call any real SMS/email provider — no messaging credentials exist in this project's env vars, and actual message delivery is outside the spec's Tier 1/2 requirements.

**A real bug this phase caught, worth knowing about:** several tool functions called `save_context` (a flush) *after* their own `db.commit()` for the primary action — so the final state change (e.g. `checkout()` setting the session's `order_id`) was silently discarded once the request ended. This was invisible in earlier tests because the `client` test fixture shared one continuous DB session/transaction across an entire test, so a flush-without-commit still looked correct (visible within that one shared transaction) even though it would vanish under real per-request session isolation. Fixed two ways: `save_context` now always commits (see its docstring), and `app/tests/conftest.py`'s `client` fixture was rebuilt to give each simulated HTTP request its own fresh session — matching production's `get_db()` — specifically so this class of bug can't hide again. `test_agent_context_survives_across_separate_http_requests` in `test_phase6_8_buyer_growth.py` is the regression test.

### Buyer identity + order listing (Phase 9 backend additions)

`POST /api/v1/buyers/identify` (get-or-create by phone) is the identity bridge the frontend, and `_ensure_commerce_session` in the voice agent, both use to turn "someone with this phone number" into a real `Buyer` row. `GET /api/v1/orders` (filterable by `merchant_id`/`buyer_id`/`status`) backs the merchant dashboard's Orders tab — `order_service.get_order` (singular) already existed from Phase 2, this adds `list_orders`.

## Frontend

### Tech stack
- **Next.js** (React, TypeScript)
- **pnpm** package manager
- **LiveKit Agents UI** (shadcn-based components) for the voice page
- **Tailwind CSS**
- Plain `fetch` + React state for the new `/buyer` and `/merchant` pages (no extra state library — see `lib/commerce-api.ts`)

### Key files
- `frontend/app-config.ts` — branding, feature flags, accent colors, visualizer config
- `frontend/app/page.tsx` — voice UI (unchanged)
- `frontend/app/buyer/page.tsx` — AI Buyer demo: identify → search/compare → cart → checkout → confirm → pay (real Razorpay Checkout if keys are configured, a friendly message if not) → audit trail
- `frontend/app/merchant/page.tsx` — merchant dashboard: Overview/Products/Orders/AI Growth/Audit tabs, all from live backend data
- `frontend/lib/commerce-api.ts` — typed client for every commerce endpoint the frontend calls; reads `NEXT_PUBLIC_COMMERCE_API_URL`
- `frontend/components/bazaar-commerce/top-nav.tsx` — small floating nav linking Voice/AI Buyer/Merchant, added to `app/layout.tsx` so it's present on every page without touching the voice page itself
- `frontend/app/api/token/route.ts` — LiveKit token endpoint
- `frontend/components/app/` — app-level logic (welcome view, view controller, theme)
- `frontend/components/agents-ui/` — voice UI components (visualizers, controls, chat)

**How the new pages were verified:** `npx tsc --noEmit` (zero errors) and a full `pnpm build` — the real build only fails in this sandbox because `next/font/google` needs `fonts.googleapis.com`, which isn't reachable here; swapping in a stub font object for that one diagnostic run confirmed both `/buyer` and `/merchant` compile and generate cleanly with the real font restored immediately after. ESLint and Prettier are clean on every new/touched file.

### Running the frontend
```bash
cd frontend
pnpm install
pnpm dev
```

### Environment variables
Copy `frontend/.env.example` to `frontend/.env.local`. Required:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `AGENT_NAME` (optional — set to `my-agent` for explicit dispatch)
- `NEXT_PUBLIC_COMMERCE_API_URL` — the FastAPI backend's URL (defaults to `http://localhost:8000`), used by `/buyer` and `/merchant`

### Linting
```bash
pnpm lint         # ESLint
pnpm format:check # Prettier
```

## Common tasks

### Change what the agent does
Edit `SYSTEM_PROMPT` in `backend/src/agent.py`. See `backend/README.md` for example prompts.

### Change the voice
Edit the `voice` argument in `murf.TTS(...)` in `backend/src/agent.py`. Browse voices at https://murf.ai/api/docs/voices-styles/voice-library.

### Add a tool to the agent
Add a method to the `Assistant` class in `backend/src/agent.py` with the `@function_tool` decorator. There's a commented example (weather lookup) in the file. Import `function_tool` and `RunContext` from `livekit.agents`.

### Switch the LLM
Replace the `llm=google.LLM(...)` call in `agent.py`. For OpenAI: install `livekit-agents[openai]`, set `OPENAI_API_KEY`, import `openai` from `livekit.plugins`, and use `openai.LLM(...)`.

### Change frontend branding
Edit `frontend/app-config.ts` — company name, page title, logo paths, accent colors, button text, visualizer type.

## Documentation references

- Murf Falcon TTS: https://murf.ai/api/docs/text-to-speech/streaming
- Murf Voice Library: https://murf.ai/api/docs/voices-styles/voice-library
- LiveKit Agents SDK: https://docs.livekit.io/agents
- LiveKit Agents UI: https://livekit.io/ui
- Deepgram STT: https://developers.deepgram.com
