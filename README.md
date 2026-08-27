# metafora.care

Clinical voice intake. A patient opens a link, talks to an AI interviewer, and
the conversation becomes a structured intake record for a clinician. This page
is how to run what exists today; everything else is in [`docs/`](./docs), the
only place documentation lives.

| | |
|---|---|
| [`docs/system-map.md`](./docs/system-map.md) | The pieces, how they talk to each other, and the rules that keep that shape. |
| [`docs/roadmap.md`](./docs/roadmap.md) | The order the rest of the work happens in, and which phase deployment, testing, auth and persistence land in. |
| [`docs/deployment.md`](./docs/deployment.md) | Where the pieces run, and what in the code once blocked a deploy. |
| [`docs/roadmap-review.md`](./docs/roadmap-review.md) | A critical read of the roadmap against the code, and what came of it. |
| [`docs/agent-review-and-pipecat-decision.md`](./docs/agent-review-and-pipecat-decision.md) | The review that chose Pipecat and the Python backend. |
| [`docs/ux/`](./docs/ux) | The frozen design specs, one per app. |

## The backend is one Python process

The conversation loop, the HTTP API, and the session log are a single FastAPI +
[Pipecat](https://docs.pipecat.ai/) process (`uv run python -m services.core.app`).
Pipecat's LiveKit transport replaces the old media service, so the SFU is the
only other thing that runs.

```
browser taps Start
  → POST /session          create record, join room *before* minting the token
  → LiveKit transport      the assistant is already in the room
  → Groq STT (whisper)     speech → text
  → SafetyGate             before the context, before the model — in the path
  → two passes at once     speech (no tools, heard) ‖ capture (tools, silent)
  → Groq TTS (Orpheus)     trimmed, 200-char capped, synthesise-ahead
  → transport output       paced back into the track
  → EndOfInterview         hang up once the last answer has been spoken
  → session log            every step, to logs/<sessionId>.jsonl
```

**Why two passes.** gpt-oss emits speech *or* a tool call in a turn, never both
— re-verified against the live API on 23 August 2026, where it returned a tool
call and empty `content` in 5 trials out of 5. A single pass holding the tool
schema therefore goes silent on exactly the turn the patient just answered. So
the two run concurrently in a `ParallelPipeline`: the speech pass carries no
tools and starts streaming immediately, and the capture pass writes the record
while that audio is still playing. Only the capture pass is ever told that
tools exist — telling the speech pass to "call update_intake" without giving it
the schema is an instruction it can only obey by reading the call out loud, and
it did, to a patient. `tests/test_prompts.py` holds that line.

The call ends itself: the capture pass records each answer with `update_intake`,
the interview machine steps, and when every question is answered the pipeline
says the goodbye, tells the browser the call ended, and closes the room. The
safety gate can also end a call, in its own sentence — it speaks first and
drains, because tearing the pipeline down immediately left the patient in a
silent call that never ended.

## Running it

You need `uv`, Node ≥ 20, a `GROQ_API_KEY`, and the LiveKit SFU.

```bash
brew install livekit          # one Go binary, no dependencies
uv sync                       # one venv, one lockfile
npm install                   # frontend only
cp .env.example .env          # then put your GROQ_API_KEY in it
```

All three at once, in one terminal — ctrl-c stops all of them:

```bash
make dev
```

Or a terminal each, if you'd rather read the logs apart:

```bash
uv run python -m services.core.app   # the whole backend, on :3000  (make api)
livekit-server --dev                 # SFU on :7880                 (make sfu)
npm run dev:call                     # patient portal, on :5173     (make web)
```

`make help` lists the rest — `make stop` frees the dev ports when something is
still holding one, `make check` runs everything below in one go.

Open <http://localhost:5173> and tap **Start the conversation** once. That is
the only tap in the interview.

## Checks

```bash
uv run pytest                  # 73 tests — no API key, no LiveKit needed
uv run ruff check .            # lint
npm run typecheck              # frontend + the generated contracts
npm run build:call             # production build of the patient portal
```

`tests/test_contracts_generated.py` fails if `shared/contracts/src/*.ts` is
stale — the TypeScript is generated from the pydantic models and never
hand-mirrored.

There is also an end-to-end harness that needs a live backend and a real key:

```bash
uv run python tests/e2e/patient.py <roomName>
```

It joins the room the way the browser does, plays a fixture WAV, and prints
what came back over the data channel.

Regenerate the contracts after changing a pydantic model:

```bash
uv run python scripts/gen_contracts.py
```

> **Free-tier limit:** Orpheus TTS allows 10 requests/minute and 100/day. One
> interview costs roughly 8–15, so expect a handful of runs per day before a
> 429.

Each call writes `logs/<sessionId>.jsonl` — both sides of the conversation,
every state transition, every safety decision, per-turn latency. Audio
recording is not wired up yet.

```bash
jq -c 'select(.type=="latency.turn")' logs/*.jsonl
jq -c 'select(.type=="safety.scanned")' logs/*.jsonl
```
