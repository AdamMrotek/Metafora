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

In the code now, not hypothetical.

1. **Cost and abuse control — do this first.** `POST /session` is unauthenticated and starts an
   LLM + TTS session on every hit. A shared link, a crawler, or one bored person is an unbounded
   Groq bill. Needs a **concurrent-session cap** (returning a friendly "all lines busy"), a
   **max call duration**, and basic **rate limiting**. The box only handles 2–4 concurrent calls
   anyway, so the cap is honest rather than a missing feature.

2. **`LIVEKIT_URL` is doing two incompatible jobs.** `app.py` dials it (`url=LIVEKIT_URL`) *and*
   hands the same string to the browser in the `/session` response. In production the backend
   may want an internal address while the browser needs the public `wss://` one. Split into two
   vars.

3. **`config.py` defaults to `devkey`/`secret`.** A misconfigured production box silently boots
   with published dev credentials and mints valid tokens. Should hard-fail on startup outside
   dev. Same for a missing `GROQ_API_KEY`: today it only logs a warning and then dies at the
   first turn, mid-call, in front of a patient.

4. **Session logs land on ephemeral disk.** `services/agent/session_log.py:198` writes
   `logs/<sessionId>.jsonl` under the repo root. On any container platform that is gone at the
   next deploy. Needs a volume, or §6.

5. **Graceful drain looks truncated.** `teardown()` awaits `stop_when_done()` and then
   immediately `task.cancel()`s the runner. `stop_when_done()` queues an `EndFrame` and returns
   — it does not wait for the pipeline to finish speaking. So the "a patient gets a goodbye
   instead of silence" guarantee in `drain()`'s docstring probably does not hold on SIGTERM.
   **Verify before relying on it for deploys.**

6. **`queue.py` is one hardcoded patient.** Everyone who opens the deployed URL becomes Alice
   (in their own room — concurrent visitors do not collide, they are just all called Alice). Fine
   for a demo; state it plainly rather than let a reader discover it.

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

1. **Ship the thin version.** Fly + LiveKit Cloud + Pages, plus blockers 1, 2, 3 (and 5
   verified). A working clickable link beats an unfinished better architecture.
2. **Then Postgres.** Sessions and transcripts persisted, `store.py` backed by it, a read-only
   clinician view over real data.
3. **Then the post-call queue**, if still wanted — now pointed at work that genuinely needs it.
