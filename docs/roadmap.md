# metafora.care — roadmap

**What this is.** The order the remaining work happens in, and — the part that keeps getting
deferred by default rather than by decision — which phase each cross-cutting concern lands in
and why. Deployment, testing, metrics, error handling, Supabase, auth: every one of them has a
row in §1 and a reason.

**Status:** draft · living document. Decisions dated 2026-08-24. Move a phase, change the page.

Read [`system-map.md`](./system-map.md) for what the pieces are and
[`deployment.md`](./deployment.md) for where they run; this page is only the order.

---

## 0 · Where we are

One working half of a product. A FastAPI + Pipecat process runs a real voice interview and a
~570-line patient portal renders it. Everything else in `system-map.md` is unbuilt: no
persistence, no auth, no clinician dashboard, no studio, no deploy, no CI. `services/core/app.py`,
`store.py` and `queue.py` have **no test coverage at all**, and they are where every deploy
blocker lives.

### The four decisions this page rests on

| | |
|---|---|
| **Ambition** | **Portfolio demo on synthetic data.** [`deployment.md` §2](./deployment.md)'s trade stands — LiveKit Cloud, no self-hosted media, no PHI posture. If this ever carries a real patient, that section is the first paragraph to revisit. |
| **MVP line** | **deploy → intake → return → review → sign**, including the escalation band. Ships end of Phase 5, not Phase 1. |
| **First deploy** | **After persistence and auth** (Phase 3). One deploy of something durable rather than two of something that evaporates. |
| **Supabase** | **Postgres (P1) → Auth (P2) → Storage (P6+)**, in that order. |

Deploying at Phase 3 rather than Phase 1 has two consequences, both good:

- The Fly volume for `logs/` disappears from the plan entirely. Postgres lands first, so
  [`deployment.md` blocker 4](./deployment.md) — session logs on ephemeral disk — is solved
  before it ever bites.
- *"Everyone who opens the link is Alice"* (`services/core/queue.py`) stops being acceptable at
  deploy time, because the deployed system now **keeps** what it records. That fix moves forward
  into Phase 3.

**On scope.** MVP-with-sign-off is five phases, which is a lot for one person. Phases 3, 4 and 5
each end at a state that is coherent to show someone, so the fallback if this runs long is to
stop at the end of a phase rather than in the middle of one.

---

## 1 · When each cross-cutting concern happens

Each row is a decision, not a guess.

| Concern | Phase | Why there and not earlier or later |
|---|---|---|
| **CI** | 0 | Nothing costs less, and everything after depends on it. Also the only thing that can hold invariant 1 — `make contracts` drift. |
| **Tests — HTTP surface** | 0 | `app.py`, `store.py` and `queue.py` have zero coverage, and Phase 1 *rewrites* two of the three. Doing that against a test suite instead of against nothing is the whole argument for testing first. |
| **Error handling — tier 1: fail fast** | 0 | Boot-time. `config.py` defaults to published dev credentials, and a missing `GROQ_API_KEY` only logs a warning before dying at the first turn, mid-call, in front of a patient. |
| **Error handling — tier 2: cost and abuse caps** | 0 | `POST /session` is unauthenticated and starts an LLM + TTS session on every hit. It must exist before a public URL — now Phase 3 — but the work belongs with the other `app.py` hardening. |
| **Supabase Postgres** | 1 | The unlock for everything clinician-facing, and the thing that makes a deploy worth doing. |
| **Auth — Supabase, clinicians only** | 2 | Backend-only: JWT verification in `svc-core`, read routes protected, verified with `curl`. The patient portal never gets it (rule 3). |
| **Deployment** | 3 | First public URL. Blue/green from the first release. |
| **Error reporting — Sentry or Logfire** | 3 | The moment failures stop happening in your own terminal. Wired to the `ErrorEvent` that already exists. |
| **Dashboard** | 4 | Lands against a live, authenticated backend rather than a local one. |
| **Error handling — tier 3: mid-call resilience** | 6+ | Groq outage fallback, patient reconnect, attach-and-resume. Only worth building once a dropped call has a record worth resuming, and not urgent for a demo. |
| **Metrics — `store-metrics`** | 6+ | Deliberately last. `session_log.py` already emits `latency.turn`, `safety.scanned` and `endpoint.decision`, and `make latency` / `make safety` already query them. A metrics *store* is for traffic that does not exist yet. Rule 5 — nothing medical — constrains the writer's shape when it is built. |
| **Supabase Storage — audio** | 6+ | Needs the post-call queue to have somewhere to put things, and a retention policy to mean anything. |

---

## 2 · Phase 0 — make it deployable

No new features. Everything here is a precondition of both Phase 1 and the deploy.

**Land the pending work first.** `services/agent/config/protocol.py`,
`tests/test_protocol_preop.py` and the `test_prompts.py` / `test_safety.py` changes are
uncommitted. Commit them before touching anything else.

**CI** — `.github/workflows/ci.yml`; the directory does not exist yet.

- `make check` — pytest, ruff, `npm run typecheck`.
- Plus `make contracts && git diff --exit-code shared/contracts/src`. CI is the only thing that
  can enforce invariant 1.

**Tests for the untested layer** — new `tests/test_app.py` and `tests/test_store.py`.

- FastAPI `TestClient` with `services.agent.pipeline.build_bot` monkeypatched to a fake bot: a
  fake `transport` with `event_handler`, a fake `worker`, a fake `wire`. That is the whole trick —
  `app.py` never needs LiveKit to be testable, so the suite keeps its no-key, no-SFU contract.
- Cover the ordering guarantee (`app.py:77-158` — the room is joined *before* the token is
  minted), 404 on an unknown session id for `/typed` and `/end`, `teardown()` idempotency,
  `drain()` across several live sessions, and the new cap returning 503.
- Reuse the fixture style already in `tests/fixtures/`.

**Config fail-fast** — `services/core/config.py`.

- Add `METAFORA_ENV` (`dev` | `prod`). Outside `dev`, hard-fail at import on a missing
  `GROQ_API_KEY`, or on `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` still equal to `devkey` /
  `secret`.
- Split `LIVEKIT_URL` — what the backend dials — from `LIVEKIT_PUBLIC_URL`, what `app.py:151`
  hands the browser. One variable is doing two incompatible jobs. Update `.env.example`.

**Caps** — `services/core/app.py`.

- `MAX_CONCURRENT_SESSIONS`, default 3, per `deployment.md` §1's 2–4 estimate. Over it,
  `start_session` returns 503 with a body the portal can render as *"all lines busy"*. The box
  genuinely cannot do more, so this is honesty rather than a limitation.
- `MAX_CALL_SECONDS` watchdog per session — a task alongside the one in `_tasks` that calls
  `teardown(id, "max_duration")`.
- Per-IP rate limit on `POST /session`. One process, so a small in-process token bucket is
  honest; `slowapi` if you would rather not hand-roll it.

**Blocker 5 — settled, and it was not where this said it was.** `drain()` was accused of awaiting
`stop_when_done()` and then cancelling the runner. It no longer does: `_await_goodbye`
(`app.py:380`) shields the runner task and waits for it to *end*, which is the EndFrame actually
reaching the transport, with `GOODBYE_TIMEOUT_S` as the ceiling.

A real SIGTERM mid-call confirmed the backend side — `logs/s_92c852d7.jsonl` has `session.ended` at
16:26:18.493 and `tts.spoken` (emitted on `BotStoppedSpeakingFrame`) at 16:26:26.670, so the
pipeline pushed all 8.2 remaining seconds of the sentence. The patient still heard it cut off after
about two, because the *portal* hung up on a 2.5s timer (`DRAIN_MS`) started when `ended` arrived —
and `ended` is sent at the top of teardown, before the goodbye has been spoken.

The lesson is worth keeping: the goodbye is a property of the whole path, and the server holding
the line open means nothing if the client will not listen. `useCall.ts` now waits for the assistant
to *leave the room* — which the bot does only once its pipeline has stopped speaking — drains the
jitter buffer, and treats a timer purely as a backstop, held above `GOODBYE_TIMEOUT_S` by a comment
in both files.

Also fixed in passing: `_wire_reason` reported a shutdown to the browser as `"error"`. Phase 3
makes shutdown routine, so the wire gained `"interrupted"` — nothing failed, this side chose to
stop, and the patient should be offered a way back rather than a fault.

**Done when** `make check` is green in CI, a SIGTERM mid-call produces a goodbye *that the patient
hears to the end*, and a fourth concurrent caller gets a polite 503.

---

## 3 · Phase 1 — persistence, Supabase Postgres

The largest single unlock. Replaces in-memory state *and* ephemeral JSONL in one move.

**Schema layout.** One Supabase Postgres, four **schemas** matching the store cut in
`system-map.md` — `clinical`, `transcript`, `config`, `metrics`. That keeps the boundary that
matters without four databases. Migrations go in the existing empty
`db/{clinical,transcript,config,metrics}/`.

| Table | Holds |
|---|---|
| `config.protocols` | `ProtocolVersion` as jsonb, immutable rows. `PROTOCOLS` in `services/agent/config/protocol.py` becomes a seed. |
| `clinical.patients` | Synthetic patients. |
| `clinical.interviews` | `(id, protocol_id, patient_id, status, scheduled_for, outcome, started_at, ended_at)`. **This is the queue** — `deployment.md` §6 is right that a status column, not pgmq, is what dispatch wants. |
| `transcript.events` | Append-only, exactly the `session_log.py` event shapes. |
| `clinical.results` | Captured fields per interview, for the review composer. |

The code changes are small because the seams already exist:

- `session_log.py:166` already defines a `SessionWriter` Protocol. Add `PostgresSessionWriter`
  beside `JsonlSessionWriter` and keep JSONL as the dev writer. **Nothing else in
  `services/agent/` changes** — the agent still writes its own transcript (rule 2).
- `services/core/store.py` splits in two. The live handles — `bot`, `machine`, the asyncio task —
  stay a process-local dict because they cannot be persisted; the *record* goes to Postgres behind
  the same `create_session` / `get_session` / `end_session` signatures. Phase 0's
  `test_store.py` is the safety net for that rewrite.
- `queue.py:next_interview()` reads `clinical.interviews WHERE status='queued'`. Its docstring
  already promises this file is the only one that changes.

**Bookkeeping, in the same commit.** `CLAUDE.md` says *"Groq is the only egress"*. Supabase makes
that false. Edit that line, and the "no database" note in `system-map.md`, in the change that
makes them wrong — a reader who trusts the docs should not then find drift in the code.

**Done when** a completed call is a queryable row after a process restart, and `psql` can answer
*"what did the agent ask this patient?"*.

---

## 4 · Phase 2 — auth, backend only

No UI. The point is that the read surface is protected *before* it is public.

- **`shared/auth/`** — Supabase Auth for clinicians. A FastAPI dependency in `services/core/`
  verifies the Supabase JWT; roles live in `config.accounts`.
- **RLS is the coarse tenant boundary and nothing finer.** Fine-grained permission checks stay in
  `svc-core` (rule 1) so a denial can be logged and explained, rather than being
  indistinguishable from a record that does not exist.
- **Split the routes before adding to them.** `app.py` has four routes, all about running a
  session. Move to `services/core/routes/{session,interviews,patients}.py` while that is still a
  mechanical change, and add the protected read routes there: `GET /interviews`,
  `GET /interviews/{id}` (transcript, scans and captured fields), `GET /patients`.
- **`app-call` gets nothing.** Rule 3 holds: a patient never holds a credential. The session
  routes stay unauthenticated, with the Phase 0 caps as their only protection.
- Extend `tests/test_app.py`: anonymous → 401, wrong role → 403 *with a reason*, valid clinician
  token → the row.

**Verification without a UI.** Fetch a JWT from Supabase's `/auth/v1/token` for a seeded clinician
account and `curl` each read route. Keep that as a script in `scripts/`; it becomes the
dashboard's contract in Phase 4.

**Done when** every route returning clinical data refuses an anonymous caller, and a `curl` with a
real clinician JWT returns the interview from Phase 1.

---

## 5 · Phase 3 — deploy

[`deployment.md` §3](./deployment.md)'s stack, now carrying a database.

- `Dockerfile` (uv, `--frozen`) and `fly.toml`: 1 vCPU / **1 GB**, `min_machines_running = 1`.
  512 MB will not hold onnxruntime plus Silero plus SmartTurn. No volume — Phase 1 removed the
  need for one.
- LiveKit Cloud free tier → `LIVEKIT_PUBLIC_URL=wss://…`.
- Supabase connection string and service key as Fly secrets. `METAFORA_ENV=prod`, so Phase 0's
  fail-fast refuses to boot on dev credentials.
- `frontend/call` to Vercel or Cloudflare Pages with a **rewrite** of `/api/*` to the Fly host.
  Prefer the rewrite over a `VITE_API_BASE`: `useCall.ts:123` fetches a relative URL, so a
  rewrite means no frontend change, no CORS, and `ALLOWED_ORIGINS` stops mattering.
- **Kill Alice.** `queue.py` is one literal, and a public link would now fill
  `clinical.interviews` with hundreds of identical rows, because the rows persist. The demo link
  mints an ephemeral synthetic patient per visitor; real per-patient dispatch is Phase 5.
- Error reporting: Sentry or Logfire initialised in `lifespan`, with every `ErrorEvent` write in
  the session routes also reported. `/health` already returns the live-session count — keep it as
  the Fly health check.
- **Blue/green, never rolling** (`deployment.md` §5). Grace period at least as long as the longest
  interview; do not let Fly's default 30-second SIGKILL near this.
- CI gains a deploy job and a post-deploy smoke test: `make e2e ROOM=<room>` against the deployed
  backend.

**Done when** a stranger opens a URL on their phone, completes an interview, and the record is
still queryable in Supabase after the next redeploy.

---

## 6 · Phase 4 — clinician dashboard, read path

The routes already exist and are already tested. This is the UI over them.

- **`frontend/shared/`** becomes real: lift the design tokens out of the `<style>` block in
  `docs/ux/clinical-dashboard.html` into the current `tokens.css` stub.
- **`frontend/dashboard/`** — a new Vite app with a Supabase Auth client for sign-in, and the
  three screens from [`clinical-dashboard.md`](./clinical-dashboard.md):
  - **Dashboard** — greeting with count freshness, three stat tiles, the review table.
  - **Interview detail** — the transcript pane showing every safety scan against the turn it ran
    on, *including the ones that matched nothing*. `services/agent/safety.py` already produces
    this and `transcript.events` now stores it. Review composer rendered read-only.
  - **Patients** — search, plus your own list. Can slip to Phase 5 if the first two run long.
- `make dev` grows a fourth process, `make check` gains the dashboard in `npm run typecheck`, and
  the deploy pipeline gains a second static site.

**Done when** you sign in on the deployed dashboard, find the call you made from your phone, and
read its transcript with the scans that cleared.

---

## 7 · Phase 5 — close the loop · MVP ships here

- **Deployments screen** and `POST /interviews`: one patient, one protocol, one time. No batch —
  `clinical-dashboard.md` is explicit that a send covering twelve people has stopped being a
  clinical decision.
- **Signed patient link.** `POST /session` takes a one-time token from the link instead of calling
  `next_interview()`, replacing Phase 3's ephemeral-patient stopgap with real dispatch.
- **Escalations.** A `ScanResult` with `urgent_escalate` writes `clinical.escalations`, and the
  band subscribes over **Supabase Realtime** — free with the Postgres already taken, so there is
  no second push mechanism to build. Only a human clears it.
- **Sign-off ledger.** `clinical.signatures`, append-only, a `(prev_hash, record_hash, hash)`
  chain, and one irreversible Sign pinned to the head.
- **Fix the outcome model.** [`example-interview.md`](./example-interview.md) found a real gap:
  the three states in `clinical-dashboard.md` assume urgent implies the call stopped, but the
  pre-op protocol produces an interview that **completed and is also an escalation**. Add the
  fourth cell here, and give `urgent_escalate` an optional `say`, spoken alongside the generated
  reply — today only `end_call` carries one, so Ruth's acknowledgement was model-improvised and
  unreviewable.

**Done when** deploy → intake → return → review → sign runs end to end on the deployed URL.

---

## 8 · Phase 6+ — features

Independent of each other; reorder freely once the MVP is out.

1. **Agent studio** — [`agent-studio.md`](./agent-studio.md)'s scope: a form, red flags, authored
   test pairs, no runner. Cheap by then, because `config.protocols` and clinician auth both
   already exist and `machine.py` / `safety.py` need no changes. The studio is only the second way
   a `ProtocolVersion` comes into existence.
2. **Post-call queue** — summarisation and structured extraction once a call ends. The one place a
   queue is honest (`deployment.md` §6): asynchronous, retryable, and it must not run inside the
   latency-critical media process. Supabase Queues / pgmq here, never for dispatch.
3. **Supabase Storage and audio retention** — `store-media`, plus a deletion schedule.
4. **Mid-call resilience** — Groq outage fallback, TTS failure recovery, patient reconnect, and
   attach-and-resume (`system-map.md` §4). Needs `transcript.events` to enforce the write fence.
5. **`store-metrics`** — a closed set of typed fields, so a transcript line has nowhere to go
   (rule 5). A Supabase view or Grafana over it.
6. **`services/comms/`** — invitation emails and reminders, then telephony, so an interview can
   reach a patient who never opens a link.

---

## 9 · How each phase is verified

| Phase | Check |
|---|---|
| **0** | `make check` green in CI, with `test_app.py` passing with no LiveKit and no API key. Manually: run `make sfu`, `make api` and `make web` in separate terminals — *not* `make dev`, whose ctrl-c takes the SFU down with the API and so proves nothing — start a call, and while the assistant is mid-sentence run `kill -TERM $(lsof -ti tcp:3000)`. Confirm the sentence finishes in your ear before the room drops. Open a fourth concurrent session and confirm the 503. |
| **1** | Complete a call, restart the process, query `clinical.interviews` and `transcript.events` for it. Keep both writers on for one call and diff the Postgres rows against the JSONL the same call wrote — same events, same order. |
| **2** | `curl` every read route anonymously (401), with a wrong-role JWT (403, *with a reason in the body*, not a 404), and with a valid clinician JWT (the row). `make check` covers the same three. |
| **3** | Open the deployed URL on a phone that is not on your network and complete an interview. Confirm the row survives a redeploy. Force an error on a staging machine and confirm it reaches Sentry. Deploy twice during a live call: it survives the first and drains with a goodbye on the second. |
| **4** | Sign in on the deployed dashboard, find that call, confirm the transcript shows cleared scans and not only matched ones. Sign in as a second account with no assignment and confirm the denial is explained rather than presented as a missing record. |
| **5** | The full loop on synthetic data: deploy an interview to a synthetic patient, open the link as them, trip `rf_fitness_change` from `example-interview.md`, watch the band appear within seconds, review and sign. Confirm the signature chain verifies and a second Sign is refused. |

Throughout: `make contracts` after any change to `shared/contracts/models.py`. CI enforces it.
