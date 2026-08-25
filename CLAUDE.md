# Metafora — orientation

Clinical voice intake. One FastAPI + Pipecat process runs the HTTP API *and* the conversation.
Python 3.12, `uv`. Frontend is one Vite/React app. ~5k lines total — small enough to read.

## Commands

`make help` lists all. The ones that matter:

| | |
|---|---|
| `make dev` | SFU + backend + portal together (ctrl-c stops all) |
| `make check` | test + lint + typecheck — everything CI runs |
| `make test` | `uv run pytest` — no API key, no LiveKit needed |
| `make contracts` | regenerate `shared/contracts/src/*.ts` after editing a pydantic model |
| `make stop` | free the dev ports (:3000 :5173 :7880) |

Always `uv run <cmd>`, never bare `python`/`pytest`.

## Map

**`services/core/`** — HTTP + state. `app.py` is the *application* — lifespan, CORS, error body,
`/health` — and the routes live in `routes/`, split by audience: `session.py` is the patient's and
holds no credential, `interviews.py` + `patients.py` are the clinician's and every route is behind
`require_role`. `lifecycle.py` is the call itself (join, speak, teardown, drain), shared by the
session router and `lifespan`. `store.py` live session handles here, the durable record in
Postgres · `reads.py` the dashboard's SQL, every function taking a `CurrentUser` · `db.py` the only
pool · `tokens.py` LiveKit tokens · `config.py` env · `queue.py` dispatch (`resolve_interview`).

**`services/agent/`** — the conversation.
- `pipeline.py` — assembles the Pipecat pipeline. **Start here** for anything about call flow.
- `machine.py` — interview state machine; `config/protocol.py` is the hardcoded question set.
- `prompts.py` — the two system prompts (speech / capture). `capture.py`, `tools.py` — tool pass.
- `safety.py` + `gate.py` — deterministic red-flag gate, runs before generation.
- `tts.py`, `tts_text.py` — Groq/Orpheus chunking and trimming. `end_call.py` — hangup.
- `wire.py` (→ browser), `observer.py` + `session_log.py` (→ `logs/<sessionId>.jsonl`).
- `config/tuning.py` — endpointing/barge-in constants. Treat as clinical, not incidental.

**`shared/contracts/`** — `models.py` + `wire.py` are the source of truth; `src/*.ts` is **generated**.

**`shared/auth/`** — every authorisation decision, and the only place one may live.
`jwks.py` the project's public keys, cached by `kid`, one rate-limited refetch when a rotation
arrives · `verify.py` ES256/RS256 only, issuer and audience always asserted · `accounts.py`
`config.accounts` → `CurrentUser` · `deps.py` `require_role` / `ClinicalReader`. Knows nothing
about this repo: `app.py`'s `lifespan` hands it its issuer, its keys and its directory.

**`supabase/migrations/`** — the schema, applied. Four schemas (`clinical`, `transcript`,
`config`, `metrics`); `config.protocols` and `transcript.events` are append-only by trigger.
`config.accounts` is **seeded by a migration, never by the application** — signing up must not be
the same act as being granted a caseload. `make test-pg` runs them against a throwaway Postgres.

**`frontend/call/src/`** — patient portal. `App.tsx`, `call/useCall.ts` is the LiveKit hook.

**`tests/`** — mirrors module names (`test_gate.py`, `test_machine.py`, …). `test_auth.py` is in
plain `make test`: it generates an EC keypair and serves the JWKS from memory, so the real ES256
path runs with no network and no project. `test_reads.py` and `test_persistence.py` are behind the
`postgres` marker. `tests/e2e/patient.py` needs a live backend and a real key; it is not part of
`make test`.

**`docs/`** — the only place prose lives. `system-map.md` = *intended* architecture,
`agent-review-and-pipecat-decision.md` = why Pipecat/Python. Read only for architectural tasks.

**Does not exist yet** (don't go looking): the dashboard and studio apps in
`system-map.md` are unbuilt; `docs/ux/*.html` are frozen specs, not running code.
`frontend/shared/` is a stub (`tokens.css` only). Audio recording/retention is unbuilt —
`store-media` in `system-map.md` is its intended home; clinical-research regulation may require it.

**Skip**: `logs/` (~460 files), `node_modules/`, `.venv/`, `frontend/**/dist/`, `*.jsonl`, `.*_cache/`.

## Invariants

1. `shared/contracts/src/*.ts` is generated, never hand-edited. `make contracts` after model changes.
2. Two LLM passes in a `ParallelPipeline` — speech (no tools) and capture (tools, silent) — because
   the model emits speech *or* a tool call, never both. Never give the speech pass tools.
   `tests/test_prompts.py` holds that line.
3. Nothing medical goes to metrics/telemetry.
4. Every authorisation decision lives in `shared/auth/` — never an `if user.role == …` in a route
   body. `require_role` yields a `CurrentUser` that the query functions in `reads.py` take as a
   **parameter**, so identity reaches the SQL and not just the door. A system that checks the role
   at the door and then queries unscoped is the one that cannot be retrofitted.
5. A patient never holds a credential. `POST /session` and its two siblings are unauthenticated by
   design; that is why the routers are split by audience.

## Conventions

Conventional commits (`feat:`, `refactor:`, `docs:`). ruff only — no mypy, no black; `make typecheck`
is TypeScript only, Python is unchecked. Two egresses, both named: Groq for STT/LLM/TTS and
Supabase for the record (Postgres) and the signing keys (`/auth/v1/.well-known/jwks.json`).
`GROQ_API_KEY` is the one env var `make dev` requires; `DATABASE_URL` and `SUPABASE_URL` are both
optional in dev and required outside it — empty `DATABASE_URL` means JSONL on disk and an
in-process store, empty `SUPABASE_URL` means the clinical routes refuse everyone with 503, which
is a refusal and never an open door. `scripts/auth.sh` signs in and calls a clinical route, which
is the dashboard's contract before the dashboard exists. `.env.example` has the rest, all dev
defaults.
