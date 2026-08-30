# Metafora — orientation

Clinical voice intake. One FastAPI + Pipecat process runs the HTTP API *and* the conversation.
Python 3.12, `uv`. Two Vite/React frontends — the patient's and the clinician's. Small enough to read.

## Commands

`make help` lists all. The ones that matter:

| | |
|---|---|
| `make dev` | SFU + backend + both portals together (ctrl-c stops all) |
| `make check` | test + lint + typecheck — everything CI runs |
| `make test` | `uv run pytest` — no API key, no LiveKit needed |
| `make contracts` | regenerate `shared/contracts/src/*.ts` after editing a pydantic model |
| `make stop` | free the dev ports (:3000 :5173 :5174 :7880) |

Always `uv run <cmd>`, never bare `python`/`pytest`.

## Map

**`services/core/`** — HTTP + state. `app.py` is the *application* — lifespan, CORS, error body,
`/health`, and `/config`, which hands a browser the Supabase project and publishable key so
neither frontend bakes one in — and the routes live in `routes/`, split by audience: `session.py` is the
patient's and holds no credential, `interviews.py` + `patients.py` + `me.py` are the clinician's
and every route is behind `require_role`. `lifecycle.py` is the call itself (join, speak, teardown, drain), shared by the
session router and `lifespan`. `store.py` live session handles here, the durable record in
Postgres · `reads.py` the dashboard's SQL — every function takes a `CurrentUser`, and the
review table is **ordered, searched, filtered and paged here**, never in the browser · `db.py` the only
pool · `tokens.py` LiveKit tokens · `config.py` env · `queue.py` arrival (`resolve_interview`:
a token, or the demo) · `dispatch.py` queueing a call for a named person, the only writer of
`clinical.patients.clinician_email` · `invitations.py` the link, whose token is *derived* from
`(interview_id, nonce)` under `INVITE_SECRET` rather than drawn, so a second copy returns the link
already sent and the table still stores only its hash.

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
`clinical.invitations` deliberately is **not** — `opened_at` and `revoked_at` are the point of it,
and a record that cannot record that a link was spent cannot refuse the second use.
`config.accounts` is **seeded by a migration, never by the application** — signing up must not be
the same act as being granted a caseload. Two more seeds, same rule: the ten-patient demo roster
(`clinical.patients`, with a `nhs_number` CHECK that admits only NHS England's 999 test range, so
this deployment *cannot* hold a real identifier) and `metrics.experience_responses`. `make test-pg`
runs them against a throwaway Postgres.

**`frontend/call/src/`** — patient portal. `App.tsx`, `call/useCall.ts` is the LiveKit hook,
`portal.css` its components.

**`frontend/dashboard/src/`** — clinician portal, the read path (`:5174`). `main.tsx` boots in one
order that cannot be reshuffled: `/config` → Supabase client → `GET /me` → the app. `api.ts` is
every request (`get`, and `post` for dispatch), `data.tsx` splits the reads by what a number is
*about* — `/overview` and `/patients` are fetched once and shared because they describe the whole
caseload, while `useInterviews` fetches one page of the review table and is not shared because it
changes on every keystroke — `router.tsx` is forty lines over `pushState`, `screens/` is the four
screens, `CopyLink.tsx` mints and copies a patient's link. **`demo.ts` is every value on the screen that
no query produced** — now only the referral reason, the consent chip and the ledger hashes,
deterministic from a real id, and the last file Phase 5 has to delete; NHS numbers, DOB and the
experience chart became seeded rows in Phase 5·0 and are formatted by `format.ts` like any other
real value. `dashboard.css` is
`docs/ux/clinical-dashboard.html`'s `<style>` block, moved rather than reinterpreted.

**`frontend/shared/tokens.css`** — tokens and base, and only those: the set all three surfaces
share. Components live with their surface, because `.app` means one thing to the portal and
another to the clinical shell.

**`tests/`** — mirrors module names (`test_gate.py`, `test_machine.py`, …). `test_auth.py` is in
plain `make test`: it generates an EC keypair and serves the JWKS from memory, so the real ES256
path runs with no network and no project. `test_reads.py`, `test_persistence.py`, `test_dispatch.py`,
`test_invitations.py` and `test_table.py` are behind the `postgres` marker.
`tests/e2e/` is everything that needs the real thing running. `patient.py` is a synthetic patient
over LiveKit — it proves the *media* path and takes a live backend and a real key.
`test_lifecycle_browser.py` is behind the `browser` marker (`make test-e2e`): `conftest.py` boots an
SFU, a backend and Vite on their own ports and drives headless Chromium at them. Two properties —
that a patient who has gone stops costing one of the three concurrency slots, and that a call that
ends is both *told* to the browser over the data channel and *filed* under why it ended. Only two of
the seven can fail on their own: `never_connects` (the page-teardown cases pass without the fix they
look like they cover, because `livekit-client` leaves the room on `pagehide` by itself) and
`filed_under_why_it_ended`, which is the only one that reaches the ending the pipeline starts —
where the reason is not settled until `_run`'s `finally` and anything ending the session in that gap
overwrites it. Check a new test against a deliberately broken build before trusting it. Neither is part of
`make test`, which stays keyless.

**`docs/`** — the only place prose lives. `system-map.md` = *intended* architecture,
`agent-review-and-pipecat-decision.md` = why Pipecat/Python. Read only for architectural tasks.

**Does not exist yet** (don't go looking): the studio app in `system-map.md` is unbuilt; dispatch
shipped at Phase 5a, but escalations and the signature ledger (5b, 5c) have not.
`docs/ux/*.html` are frozen specs, not running code. Audio recording/retention is unbuilt —
`store-media` in `system-map.md` is its intended home; clinical-research regulation may require it.

**Skip**: `logs/` (~460 files), `node_modules/`, `.venv/`, `frontend/**/dist/`, `*.jsonl`, `.*_cache/`.

## Invariants

1. `shared/contracts/src/*.ts` is generated, never hand-edited. `make contracts` after model changes.
2. Two LLM passes in a `ParallelPipeline` — speech (no tools) and capture (tools, silent) — because
   the model emits speech *or* a tool call, never both. Never give the speech pass tools.
   `tests/test_prompts.py` holds that line.
3. Nothing medical goes to metrics/telemetry.
4. A count on the screen is a count over the caller's whole scope, never over the rows that
   happen to be on the page. The tiles, the escalation band and the patients table's pills are
   `reads.overview` and `reads.patients` for that reason: the browser used to derive them from
   the review table's own fetch, which was right only while that fetch was the entire record.
5. Every authorisation decision lives in `shared/auth/` — never an `if user.role == …` in a route
   body. `require_role` yields a `CurrentUser` that the query functions in `reads.py` take as a
   **parameter**, so identity reaches the SQL and not just the door. A system that checks the role
   at the door and then queries unscoped is the one that cannot be retrofitted.
6. A patient never holds a credential. `POST /session` and its two siblings are unauthenticated by
   design; that is why the routers are split by audience. An invitation token is not a credential:
   it names one interview, is spent when that call starts, and grants nothing else.

## Conventions

Conventional commits (`feat:`, `refactor:`, `docs:`). ruff only — no mypy, no black; `make typecheck`
is TypeScript only, Python is unchecked. Three egresses, all named: Groq for STT/LLM/TTS,
Supabase for the record (Postgres), the signing keys (`/auth/v1/.well-known/jwks.json`) and the
clinician's sign-in — which the dashboard's *browser* makes directly, with the publishable key
`/config` hands it, so it is the same egress and not a fourth — and
Sentry for failures — which carries no part of the product by construction, because `app.py`
gives it no request bodies and drops anything raised inside `services/agent/`.
`GROQ_API_KEY` is the one env var `make dev` requires; `DATABASE_URL`, `SUPABASE_URL`,
`PORTAL_URL` and `INVITE_SECRET` are all optional in dev and required outside it (`Dockerfile` + `fly.toml` deploy the backend, and
`config.py` names every secret it refuses to boot without) — empty `DATABASE_URL` means JSONL on disk and an
in-process store, empty `SUPABASE_URL` means the clinical routes refuse everyone with 503, which
is a refusal and never an open door. `SUPABASE_PUBLISHABLE_KEY` is in neither list: it is read
only by `/config`, and empty means the dashboard is told so rather than the box refusing to boot — a
clinician's sign-in key must not be able to take the patient path down. `scripts/auth.sh` is the
same contract from a shell, and it was written before the dashboard was. `.env.example` has the
rest, all dev defaults.
