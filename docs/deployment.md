# metafora.care — deployment

**What this is.** How the three running pieces get off a laptop and onto a URL, what in the
code blocks that today, and what the target is for a **portfolio deployment** — a public,
clickable demo on synthetic data, not a clinical deployment on real patients.

**Status:** draft · plan, not yet executed. Nothing here is built. If a decision below changes,
change it here rather than in a commit message.

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

No torch. The heavy dependencies are `onnxruntime` (66 MB), `llvmlite`/`numba`, and `scipy`;
the venv is ~511 MB, so a ~700–800 MB image. The SmartTurn v3 model is an 8.7 MB ONNX file
**bundled in the pipecat wheel** — there is no runtime download and no cold-start model fetch.
Import of numba/llvmlite/scipy/onnxruntime still costs seconds, which is why the backend stays
warm (§3).

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

Six were found. **Four are now closed** (roadmap Phase 0, 2026-08-24); the two that remain are
owned by later phases, so nothing on this list is loose.

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

All four are covered by `tests/test_app.py`, which fakes the LiveKit bot
(`tests/fakes.py`) so the HTTP surface is testable with no SFU, no Groq key and no network.

### Still open, and owned

4. **Session logs land on ephemeral disk.** `services/agent/session_log.py:198` writes
   `logs/<sessionId>.jsonl` under the repo root. **Owned by roadmap Phase 1** — Postgres lands
   before the deploy, so this is solved rather than papered over with a volume. See §6.

6. **`queue.py` is one hardcoded patient.** Everyone who opens the deployed URL becomes Alice —
   in their own room, so concurrent visitors do not collide, they are just all called Alice.
   **Owned by roadmap Phase 3**, and it stopped being cosmetic once the rows persist: the demo
   link mints an ephemeral synthetic patient per visitor, and real per-patient dispatch arrives
   in Phase 5.

---

## 5 · Deploy mechanic

A restart drops every live call, so **blue/green, not rolling**: bring up the new backend, stop
routing new `POST /session` to the old one, let it drain, kill it after a grace period at least
as long as the longest interview. Do not let a platform's default 30-second SIGKILL near this —
a patient mid-sentence is the failure mode. Depends on blocker 5 being real.

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

**Bookkeeping.** Adding Supabase makes it a second egress, so CLAUDE.md's *"Groq is the only
egress"* invariant becomes false. Update that line in the same change; a reader who trusts the
docs and then reads the code should not find drift.

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
