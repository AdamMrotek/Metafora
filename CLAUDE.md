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

**`services/core/`** — HTTP + state. `app.py` (4 routes, `POST /session` is the entry point) ·
`store.py` in-process session records · `tokens.py` LiveKit tokens · `config.py` env · `queue.py` dispatch.

**`services/agent/`** — the conversation.
- `pipeline.py` — assembles the Pipecat pipeline. **Start here** for anything about call flow.
- `machine.py` — interview state machine; `config/protocol.py` is the hardcoded question set.
- `prompts.py` — the two system prompts (speech / capture). `capture.py`, `tools.py` — tool pass.
- `safety.py` + `gate.py` — deterministic red-flag gate, runs before generation.
- `tts.py`, `tts_text.py` — Groq/Orpheus chunking and trimming. `end_call.py` — hangup.
- `wire.py` (→ browser), `observer.py` + `session_log.py` (→ `logs/<sessionId>.jsonl`).
- `config/tuning.py` — endpointing/barge-in constants. Treat as clinical, not incidental.

**`shared/contracts/`** — `models.py` + `wire.py` are the source of truth; `src/*.ts` is **generated**.

**`frontend/call/src/`** — patient portal. `App.tsx`, `call/useCall.ts` is the LiveKit hook.

**`tests/`** — mirrors module names (`test_gate.py`, `test_machine.py`, …). `tests/e2e/patient.py`
needs a live backend and a real key; it is not part of `make test`.

**`docs/`** — the only place prose lives. `system-map.md` = *intended* architecture,
`agent-review-and-pipecat-decision.md` = why Pipecat/Python. Read only for architectural tasks.

**Does not exist yet** (don't go looking): no database — sessions are in-memory (`core/store.py`)
plus JSONL on disk, and `db/` is empty placeholders. The dashboard and studio apps in
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

## Conventions

Conventional commits (`feat:`, `refactor:`, `docs:`). ruff only — no mypy, no black; `make typecheck`
is TypeScript only, Python is unchecked. Groq is the only egress; `GROQ_API_KEY` is the one required
env var (`.env.example` has the rest, all dev defaults). Current branch: `refactor/python-rewrite`.
