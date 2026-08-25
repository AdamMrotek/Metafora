# metafora.care — roadmap

What is left to build, in order. Portfolio demo on synthetic data — no PHI posture.
[`system-map.md`](./system-map.md) is what the pieces are, [`deployment.md`](./deployment.md) is
where they run.

**Status:** 2026-08-24.

---

## Built

- Voice interview end to end: Pipecat pipeline, state machine, safety gate, TTS, patient portal.
- CI (`make check`), tests for `app.py` / `store.py` / caps, fail-fast config.
- Concurrency cap + per-IP rate limit, both refusing before a session exists.
- Graceful goodbye on SIGTERM, heard to the end by the patient.
- Postgres schema applied — `supabase/migrations/`, project `xonnqmdzmhlchfawsquk` (eu-west-2).
  `config.protocols`, `clinical.patients`, `clinical.interviews`, `transcript.events`,
  `clinical.results`. Append-only triggers under test (`make test-pg`).
- **Persistence — Phase 1, below.** A call now survives the process that ran it.

---

## 1 · Persistence — **done**

Numbers are kept as they were: the later phases, `deployment.md` and the migrations refer to each
other by number, and shifting them would break every one of those references.

*Why it went first:* everything clinician-facing needs a queryable record, and landing it before
the deploy meant no Fly volume for `logs/`. The claim (`queued → running`) is shared by every kind
of arrival, so it was cheaper to write here than to retrofit at Phase 5.

- `PostgresSessionWriter` in `session_log.py` → `transcript.events`. **Instead of**
  `JsonlSessionWriter`, not beside it: a call takes one path or the other, decided by whether
  `DATABASE_URL` is set. Nothing in `services/agent/` else changed. `append` stays synchronous
  and non-blocking — it queues and a background task batches the inserts — because it runs on
  every turn inside the task that is decoding audio.
- `store.py` split: live handles (`bot`, `machine`, writer) stay a process dict, the record goes
  to Postgres. `create_session` / `end_session` became `async`; `get_session` / `live_sessions`
  did not, because they only read the dict.
- `end_session` also writes `clinical.results` from `machine.fields()`, so Phase 4's composer has
  rows to render without replaying a transcript.
- `queue.py` → `resolve_interview(token: str | None)`:
  - token → `clinical.invitations` lookup — stub (`UnknownInvitation`), filled in at Phase 5
  - no token → mint an ephemeral synthetic patient + interview, `origin = 'demo'`
  - both → `claim()`: `queued → running`, `SKIP LOCKED`, no-op if already running
- `services/core/db.py` — the only pool. `enabled()` reads the pool, never the environment, so a
  test that never ran `lifespan` cannot reach a database whatever `.env` says.
- Migration: `clinical.patients.origin` (`'demo' | 'dispatched'`).
- `config.protocols` seeded from `services/agent/config/protocol.py` at boot, `on conflict do
  nothing` so it survives a restart against an append-only table.
- Empty `db/*` placeholders deleted; `CLAUDE.md`, `system-map.md`, `.env.example` and
  `deployment.md` §4 corrected.
- Tests: `tests/test_persistence.py` (`make test-pg`) for the database half,
  `tests/test_queue.py` and `tests/test_store.py` for the no-database half.

**No `DATABASE_URL` is still supported**, not a fallback: JSONL on disk and an in-process store,
which is what `make dev` uses on a laptop with nothing provisioned.

**Done:** complete a call, restart the process, query the row. Two callers at once get two rows.

---

## 2 · Auth — backend only

*Why now:* the read surface has to be protected *before* it is public. Splitting `app.py` into
routes is mechanical while there are four of them and painful once there are ten.

- `shared/auth/` — Supabase JWT verification as a FastAPI dependency. Roles in `config.accounts`.
- Split `app.py` into `services/core/routes/{session,interviews,patients}.py`.
- Read routes, clinician only: `GET /interviews`, `GET /interviews/{id}`, `GET /patients`.
- Session routes stay unauthenticated — a patient never holds a credential.
- Tests: anonymous → 401, wrong role → 403 with a reason, clinician → the row.
- `scripts/` curl script fetching a real JWT. Becomes the dashboard's contract.

**Done:** every clinical route refuses an anonymous caller; curl with a JWT returns the interview.

---

## 3 · Deploy

*Why now:* one deploy of something durable rather than two of something that evaporates — the
first public URL already keeps what it records and already refuses an anonymous reader.

- `Dockerfile` (uv, `--frozen`) + `fly.toml`: 1 vCPU / 1 GB, `min_machines_running = 1`, no volume.
- LiveKit Cloud → `LIVEKIT_PUBLIC_URL`.
- Fly secrets: `DATABASE_URL`, Supabase key, `METAFORA_ENV=prod`.
- `frontend/call` → Vercel or Cloudflare Pages, `/api/*` rewritten to the Fly host.
- Sentry or Logfire in `lifespan`, every `ErrorEvent` reported. `/health` as the Fly check.
- Blue/green, never rolling. Grace period longer than the longest interview.
- CI deploy job + post-deploy smoke test.
- Label the public URL as a demo on the opening screen.

**Done:** a stranger completes an interview on their phone; the row survives a redeploy.

---

## 4 · Clinician dashboard — read path

*Why now:* the routes exist and are tested, so this is UI over them — built against a live
authenticated backend instead of a local stand-in.

- `frontend/shared/tokens.css` — real, lifted from `docs/ux/clinical-dashboard.html`.
- `frontend/dashboard/` — Vite app, Supabase Auth sign-in.
- Screens: **dashboard** (stat tiles + review table) · **interview detail** (transcript with every
  safety scan, including the ones that matched nothing; review composer read-only) ·
  **patients** (search + own list).
- `make dev` grows a fourth process; `make check` typechecks it; deploy gains a second static site.

**Done:** sign in, find the call, read its transcript with the cleared scans.

---

## 5 · Call dispatch + close the loop — MVP

*Why now:* the link replaces the ephemeral demo path with real dispatch, and because Phase 1
already claims the row, this is a lookup rather than a rewrite. Everything the MVP line promises —
deploy → intake → return → review → sign — closes here.

**Dispatch.** A clinician queues a call and the patient gets an emailed link.

- Deployments screen + `POST /interviews`: one patient, one protocol, one time, one channel.
  No batch. Row lands `status='queued'`.
- `clinical.invitations (interview_id, token_hash, channel, sent_at, expires_at, opened_at)`.
  Token is `secrets.token_urlsafe(32)`, stored hashed, bound to one interview.
- Email goes out 24h before `scheduled_for` — a small sweep of interviews due and not yet sent.
  One link, one interview, so two calls a week apart are never ambiguous.
- The link opens the patient portal; the patient starts the conversation. **The link works until
  the call starts**, then it is spent.
- `POST /session` takes the token → validate → `claim()` → run.
- Expired, spent or unknown link → a sentence on screen, not a status code.

**The rest of the loop.**

- Escalations: `urgent_escalate` writes `clinical.escalations`, the band subscribes over Supabase
  Realtime. Only a human clears it.
- Sign-off: `clinical.signatures`, append-only `(prev_hash, record_hash, hash)` chain, one
  irreversible Sign pinned to the head.
- Fourth outcome state: an interview that **completed and is also an escalation**. Give
  `urgent_escalate` an optional `say` so the acknowledgement is authored, not improvised.

**Done:** deploy → intake → return → review → sign, on the deployed URL.

---

## 6+ · Later

*Why not sooner:* none of it is on the MVP line, and each needs something the earlier phases build.
Independent of each other; reorder freely.

- **Agent studio** — form, red flags, authored test pairs, no runner.
- **Post-call queue** — summarisation and structured extraction after a call. pgmq.
- **Audio retention** — Supabase Storage + a deletion schedule.
- **Mid-call resilience** — reconnect, attach-and-resume, Groq outage fallback. A link that
  survives past the start of the call belongs here.
- **`store-metrics`** — closed set of typed fields, nothing medical.
- **`services/comms/`** — reminders, then telephony: an outbound call to a patient who never opens
  an email. This is the route the product is really for; the link is the stand-in.

Out of scope: a patient app with accounts, which is what photo capture would need.

---

`make contracts` after any change to `shared/contracts/models.py`. CI enforces it.
