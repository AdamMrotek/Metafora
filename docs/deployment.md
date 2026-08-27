# metafora.care — deployment

**What this is.** How the three running pieces get off a laptop and onto a URL, what in the
code blocks that today, and what the target is for a **portfolio deployment** — a public,
clickable demo on synthetic data, not a clinical deployment on real patients.

**Status:** 2026-08-25 · built. `Dockerfile`, `fly.toml`, `frontend/call/vercel.json`,
`scripts/smoke.sh` and `.github/workflows/deploy.yml` are the executable form of this page. If a
decision below changes, change it here rather than in a commit message.

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

## 3 · Target stack (portfolio)

| Piece | Where | Cost |
|---|---|---|
| `frontend/call` | Cloudflare Pages or Vercel — static Vite build | free |
| LiveKit | LiveKit Cloud, free tier | free |
| backend (FastAPI + Pipecat) | Fly.io — 1 machine, 1 vCPU / 1 GB, **always-on** | ~$5/mo |

**1 GB minimum, and no scale-to-zero.** A 512 MB tier will not hold onnxruntime + Silero +
SmartTurn. `min_machines_running = 1`: a cold link that shows a spinner while Python imports
numba is the whole failure mode of a portfolio deploy.

**Origins.** Splitting the frontend onto Pages costs us the same-origin arrangement dev has.
`frontend/call/src/call/useCall.ts:123` fetches a *relative* `/api/session`; there is no API
base URL in the frontend at all. Two options: add a `VITE_API_BASE`, or point the Pages
rewrite at the Fly host and keep one origin. **Prefer the rewrite** — no frontend code change,
no CORS, and `ALLOWED_ORIGINS` stops mattering.

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
