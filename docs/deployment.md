# metafora.care — deployment

**What this is.** How the three running pieces get off a laptop and onto a URL, what in the
code blocks that today, and what the target is for a **portfolio deployment** — a public,
clickable demo on synthetic data, not a clinical deployment on real patients.

**Status:** 2026-08-27 · **deployed**, and proven by a real call (§3). `Dockerfile`,
`fly.toml`, `frontend/call/vercel.json`, `scripts/smoke.sh` and
`.github/workflows/deploy.yml` are the executable form of this page. If a decision below
changes, change it here rather than in a commit message.

Read [`system-map.md`](./system-map.md) first for what the pieces are. This page only covers
where they run.

---

## 1 · The constraint

The backend is **stateful, long-lived, and session-affine**. Three things in the code force it:

- `services/core/store.py` holds sessions in a process-local dict, and `services/core/app.py`
  holds one asyncio task per call in `_tasks`.
- `POST /session` joins the LiveKit room *from the process that served the request*
  (`app.py:97-120`) — the bot **is** that process.
- `/session/{id}/typed` and `/session/{id}/end` must land on that same process, or they 404.

So: no serverless, no scale-to-zero, and no load balancer that round-robins the by-id routes.
Whatever hosts the backend has to give us long-lived processes with request affinity.

Second constraint: `LocalSmartTurnAnalyzerV3` and Silero VAD run **in-process, per call**, on
every 32 ms frame (`services/agent/config/tuning.py:24`). Groq handles STT/LLM/TTS remotely so
we are not GPU-bound, but concurrent-calls-per-vCPU is a small number to be measured, not
guessed. Assume 2–4 on a 1–2 vCPU box until someone loads it.

### Weight

No torch. Measured in the built image on 2026-08-25: the venv is **766 MB** and the image is
**1.26 GB** — `llvmlite` 169 MB, `scipy` 118 MB, `sympy` 74 MB, `onnxruntime` 46 MB, `numpy`
40 MB, `numba` 35 MB. The earlier ~700–800 MB estimate was the macOS venv; linux wheels plus
`UV_COMPILE_BYTECODE=1` (which buys the fast boot below) account for the difference. The SmartTurn v3 model is an 8.7 MB ONNX file
**bundled in the pipecat wheel** — there is no runtime download and no cold-start model fetch.
Import of numba/llvmlite/scipy/onnxruntime still costs seconds, which is why the backend stays
warm (§3) — measured cold-start to a 200 on `/health` is **4 s**.

---

## 2 · The fork we are not taking

For a *clinical* deployment, patient voice is PHI, and the CLAUDE.md invariants — *"nothing
medical goes to metrics/telemetry"*, *"Groq is the only egress"* — argue for **self-hosted
LiveKit**, so media never leaves our infrastructure. The cost is TLS on `wss`, UDP
50000–60000, and TURN over TCP/443, which matters because clinic and hospital firewalls block
UDP and that is exactly where our patients sit.

For a **portfolio** deployment on synthetic data that argument does not apply, and it was the
expensive part of the plan. So: **LiveKit Cloud free tier**, and the TURN/UDP/TLS work
disappears. This is a deliberate trade recorded here so it is not mistaken for an oversight —
if this ever carries a real patient, §2 is the paragraph to revisit first.

---

## 3 · The stack, as deployed

| Piece | Where | Cost |
|---|---|---|
| `frontend/call` | Vercel — `metafora-call`, static Vite build, Root Directory `frontend/call` | free |
| LiveKit | LiveKit Cloud, free tier | free |
| backend (FastAPI + Pipecat) | Fly.io — `metafora`, one `shared-cpu-1x` / 1 GB machine in `lhr`, **always-on** | ~$6/mo |

Live since 2026-08-27: **https://metafora-call.vercel.app** in front of
**https://metafora.fly.dev**. The first call through it completed and persisted — two turns, both
scanned before generation, both passes firing, two fields captured into `clinical.results`. That
sentence is the whole of §6's argument, so it is worth recording that it is now a fact rather
than a plan.

**1 GB minimum, and no scale-to-zero.** A 512 MB tier will not hold onnxruntime + Silero +
SmartTurn. `min_machines_running = 1`: a cold link that shows a spinner while Python imports
numba is the whole failure mode of a portfolio deploy.

**Origins — the rewrite won.** `useCall.ts` fetches a *relative* `/api/session`; there is no API
base URL in the frontend at all. `frontend/call/vercel.json` sends `/api/:path*` to
`https://metafora.fly.dev/:path*`, so the browser only ever sees one origin: no `VITE_API_BASE`,
no CORS, and `ALLOWED_ORIGINS` never has to be right.

The SFU address is not build config either. `POST /session` returns `LIVEKIT_PUBLIC_URL` in its
bootstrap (`services/core/routes/session.py:116`) and the browser connects to whatever it is
handed — so changing SFU is a Fly secret, not a rebuild. The frontend holds exactly one piece of
configuration, and it is the rewrite destination.

**The Vercel setting that is not optional.** Root Directory is `frontend/call`, and *include
files outside the root directory* must stay **on**. `@metafora/call` depends on the
`@metafora/contracts` and `@metafora/ui` workspaces at the repo root, and `vercel.json` installs
with `cd ../.. && npm ci`. Restrict the build to the root directory and those two packages stop
resolving — the same class of failure as the health check in §5: the app is fine, the platform
was not given what it needs to see.

**Cost has no ceiling at the platform, and that is not fixable.** Fly bills usage only — no fixed
plan, no budget, no billing alerts, and prepaid credits are explicitly *not* a cap: *"Credits are
not a way to control spend"*, the remainder goes to the card on file. What actually holds the Fly
line is the shape of `fly.toml` — one machine, `auto_start_machines` and `auto_stop_machines` both
false, no autoscaling path at all — so ~$6/mo cannot drift. The variable cost is Groq, and
`services/core/limits.py` already names where the real ceiling lives: its `DailyQuota` is
in-process, so a restart forgets the day's count and a deploy *is* a restart. Set the spend limit
at the provider and treat `MAX_SESSIONS_PER_DAY` as the polite brake the module calls itself.

---

## 4 · What blocks a deploy today

Six were found. **All six are now closed** — four by roadmap Phase 0 and the last two by
Phase 1, both on 2026-08-24.

### Closed

1. ~~**Cost and abuse control.**~~ `POST /session` now refuses before it creates anything:
   `MAX_CONCURRENT_SESSIONS` (default 3) returns 503, a per-IP token bucket
   (`services/core/limits.py`) returns 429 with `Retry-After`, and a `MAX_CALL_SECONDS` watchdog
   hangs up a tab left open. Both refusals carry the same "all lines busy" sentence, and both
   happen before a session record, a room or a log file exists.

2. ~~**`LIVEKIT_URL` doing two incompatible jobs.**~~ Split: `LIVEKIT_URL` is what this process
   dials, `LIVEKIT_PUBLIC_URL` is what the browser is handed. Defaults to the former, so dev is
   unchanged.

3. ~~**`config.py` defaulting to `devkey`/`secret`.**~~ `METAFORA_ENV` gates it. Outside `dev`,
   `services/core/config.py` refuses to import on the published SFU credentials, an empty
   `GROQ_API_KEY`, or a `ws://` URL a browser on https would reject — at import, which is the
   last moment the failure is a stack trace rather than a patient.

5. ~~**Graceful drain looked truncated.**~~ **It was.** Verified against the installed pipecat:
   `stop_when_done()` (`pipeline/worker.py:659`) queues an `EndFrame` and returns without
   waiting, so cancelling the runner immediately after it did cut the goodbye off. `teardown()`
   now waits for the runner task to end on its own, bounded by `GOODBYE_TIMEOUT_S`, then cancels
   and waits for the cancellation to land. Held by
   `tests/test_app.py::test_teardown_waits_for_the_goodbye_instead_of_cutting_it_off`.

4. ~~**Session logs land on ephemeral disk.**~~ Closed by Phase 1, not by a volume.
   `PostgresSessionWriter` writes `transcript.events` instead; the JSONL writer is what a laptop
   with no `DATABASE_URL` gets, and a call takes one path or the other, never both. `append`
   stays synchronous and non-blocking — it queues, and a background task batches the inserts —
   because it is called on every turn from the same task that is decoding audio.

6. ~~**`queue.py` is one hardcoded patient.**~~ `next_interview()` is gone. `resolve_interview()`
   mints an ephemeral synthetic patient per visitor (`clinical.patients.origin = 'demo'`), and
   both it and Phase 5's emailed link end at the same `claim()` — `queued → running`, `skip
   locked`, so two tabs on one link start one call.

Blockers 1, 2, 3 and 5 are covered by `tests/test_app.py`, which fakes the LiveKit bot
(`tests/fakes.py`) so the HTTP surface is testable with no SFU, no Groq key and no network.
Blockers 4 and 6 are covered by `tests/test_persistence.py`, which needs a real Postgres and runs
under `make test-pg`.

---

## 5 · Deploy mechanic

A restart drops every live call, so **blue/green, not rolling**: bring up the new backend,
health-check it, then let the old one go.

**The goodbye is the drain, and the grace period is 30 seconds — not fifteen minutes.** An
earlier draft of this section said to wait out the longest possible interview. That describes
behaviour the code does not have: `lifespan` calls `lifecycle.drain()`, which calls `teardown()`
on every live session *immediately*. `teardown` queues the goodbye and waits `GOODBYE_TIMEOUT_S`
= 10 s for the pipeline to finish saying it. So a deploy ends every call in progress; what it
does not do is end them in silence, which is what blocker 5 was about and is the difference that
was worth having. `fly.toml` sets `kill_timeout = "30s"` — that 10 s, plus room for the pool and
the pipeline to close.

Raising `kill_timeout` toward `MAX_CALL_SECONDS` would achieve nothing on its own. Letting calls
run to their natural end is a change in `lifecycle.py` (stop accepting, wait, hang up only on
timeout), and this number would follow it rather than lead it.

**Affinity.** §1 is right that `/session/{id}/typed` must reach the process holding the session.
Phase 3 runs exactly one machine, so that holds by construction. It does not hold the moment
there are two — and the measurement in §1 may well ask for two. So `POST /session` returns
`FLY_MACHINE_ID` on the bootstrap and `useCall.ts` returns it as `fly-force-instance-id`. Unused
today, tested today, so that scaling is `fly scale count` and not an afternoon of intermittent
404s that read as a bug in the pipeline.

That one machine is not the default, and Fly will argue with you about it. The first deploy —
`rolling`, because there was nothing to be blue or green against yet — created **two** machines
on its own, announcing *"Creating a second machine for high availability and zero downtime
deployments"*. For an app whose sessions are a dict in one process, the second machine is not
availability, it is a coin flip on every `/typed`. `fly scale count 1` undid it. Blue/green does
not reintroduce the problem, because it replaces one machine with one machine — but a deploy that
falls back to `rolling` will, so read the plan line before confirming.

**Blue/green needs a *machine* check, not a service check — and this cost a deploy to learn.**
The first CI deploy failed after five minutes with `could not get all green machines to be
healthy`, having deleted a green machine that had been answering `/health` with `200` every 15
seconds the entire time. `[[http_service.checks]]` are run by the proxy for machines already in
the service pool; a green machine is not in the pool yet, so the check ran and reported its result
nowhere flyctl could see it — which flyctl spells `unchecked`, then kills. `fly.toml` now defines
a named `[checks.health]` instead: run by the machine, written into the machine record, which is
what blue/green actually polls. `--wait-timeout` is a red herring — it already waited its full
five-minute default, and the status was never going to arrive.

The general shape is worth keeping, because it recurs: **the app being healthy and the platform
being able to see that it is healthy are two different things**, and every deployment failure on
this page so far has been the second one.

---

## 6 · Supabase — Postgres yes, queue no

**Postgres: yes.** The highest-value addition available. It replaces the in-memory `store.py`
and the JSONL-on-ephemeral-disk problem (blocker 4) in one move, and it unlocks the
`app-dashboard` that `system-map.md` lists as unbuilt. *One live call → persisted structured
intake → a clinician view of it* is a far better demonstration than a call that evaporates.
Auth (a patient magic link, instead of everyone being Alice) and Storage (audio retention,
which CLAUDE.md flags as unbuilt) are natural follow-ons.

**Supabase Queues / pgmq for `queue.py`: no.** A message queue solves *many producers,
competing consumers, retryable work that must survive a crash*. Dispatch has one consumer (an
HTTP handler), work that is inherently non-retryable — a patient is on the line **now**, you
cannot redeliver that — and a synchronous request/response shape: `POST /session` peeks the head
and immediately joins a room in-process. A queue there would be a queue popped inside a request
handler, which reads as cargo-cult. What dispatch actually wants is a **table with a status
column** — `interviews(id, protocol_id, patient_id, status, scheduled_for)`. That is a schedule,
not a queue.

**Where a queue is honest: post-call processing.** When a session ends there is a transcript and
captured fields, and we want summarisation, structured extraction into a clinician note, and
audio archival. That work *is* asynchronous, *is* retryable, must survive a crash — and must not
run inside the media process, which is latency-critical and CPU-bound by VAD and turn detection.
That is a textbook queue, and a better thing to build than a decorated dispatch.

**Bookkeeping.** Adding Supabase made it a second egress, so CLAUDE.md's *"Groq is the only
egress"* invariant became false. Done in the same change (Phase 1): the file now names both, and
says which one is optional in dev.

---

## 7 · Order

**Superseded by [`roadmap.md`](./roadmap.md), which phases the whole project.** This section
originally said *ship the thin version first* — deploy on ephemeral state, add Postgres after.
That was reversed on 2026-08-24: **persistence and auth land before the first deploy**, so one
deploy carries something durable rather than two carrying something that evaporates.

Two things on this page change as a result:

- **Blocker 4 stops needing a volume.** Postgres replaces the JSONL-on-ephemeral-disk problem
  before there is a container to lose it on.
- **Blocker 6 stops being acceptable at deploy time.** A public link that makes everyone Alice
  was fine while the record evaporated; once the rows persist it fills `clinical.interviews`
  with hundreds of identical patients. The demo link mints an ephemeral synthetic patient
  instead.

Everything else here still stands: the constraint in §1, the trade in §2, the stack in §3,
blockers 1, 2, 3 and 5, the blue/green mechanic in §5, and the Supabase verdict in §6. The
roadmap points at them rather than restating them.

The order itself is now: **blockers → Postgres → auth → deploy → dashboard → sign-off**, with
the post-call queue after the MVP, pointed at work that genuinely needs a queue.

As of 2026-08-27, everything up to and including **deploy** is done. Two things were deliberately
left undone rather than forgotten: `SENTRY_DSN` is unset, so a failure in production is visible
only in `flyctl logs`, and the Groq-side spend limit that §3 argues is the real ceiling is not yet
set. The dashboard is next, and it is the first consumer of `reads.py` — which is to say the first
time the auth work has anything to be authorised *for*.
