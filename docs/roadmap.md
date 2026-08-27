# metafora.care — roadmap

What is left to build, in order. Portfolio demo on synthetic data — no PHI posture.
[`system-map.md`](./system-map.md) is what the pieces are, [`deployment.md`](./deployment.md) is
where they run.

**Status:** 2026-08-25.

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
- **Auth — Phase 2, below.** The record is readable by a named clinician and by nobody else.

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

## 2 · Auth — **done**

*Why it went here:* the read surface had to be protected before it was public, and splitting
`app.py` into routers was mechanical while there were four routes and painful once there are ten.

Both decisions taken up front held. **JWKS, not a shared secret** — no symmetric credential sits
in Fly secrets and a key rotates without a redeploy. And **`config.accounts` is seeded, not
self-serve**: signing up is not the same act as being granted a caseload, which on a public demo
URL is otherwise exactly what it is.

- **`shared/auth/`** — the only place an authorisation decision lives.
  - `jwks.py` — keys cached by `kid`, pre-warmed in `lifespan`, one rate-limited refetch when an
    unknown `kid` arrives. The refetch is the feature, not a fallback: it is what makes rotation
    a thing this process discovers rather than a thing a redeploy is timed against. A refetch
    that *fails* keeps the keys already held and refuses the caller — an unreachable auth
    endpoint costs a rotation, never every clinician already signed in, and never a 500.
  - `verify.py` — `audience="authenticated"`, `issuer={SUPABASE_URL}/auth/v1`, ES256/RS256 and
    nothing else. `exp`/`iat`/`sub`/`aud`/`iss` are *required*, not merely checked-if-present:
    PyJWT does not demand `exp` on its own, and a token without one is a credential nobody can
    revoke.
  - `accounts.py` — `config.accounts` → `CurrentUser`. Writes exactly one column, `sub`, bound
    the first time a verified token arrives for a seeded email and conditional on `sub is null`
    in the SQL. A second auth user cannot then claim a bound address, which is the hole that
    deleting a Supabase user and signing up again would otherwise open.
  - `deps.py` — `require_role(...)`, and `ClinicalReader` as the annotation the routes actually
    spell. 401 for missing or invalid, 403 *with a reason* for verified-but-unknown. The two
    refusals are ordered: whether this process can verify anything is a fact about the server, so
    an unconfigured box answers 503 to everyone, including someone holding a good token.
  - It reads no environment of its own. `lifespan` hands it the issuer, the keys and the
    directory, which is what keeps `shared/` a base layer and what lets the test suite stand a
    whole authenticated backend up with an EC keypair.
- **`config.accounts`** — `(email primary key, sub unique, role, display_name, created_at)`,
  seeded by the migration. **`clinical.patients.clinician_email`** references it, nullable, and
  null means unowned: a demo visitor is nobody's patient. Phase 5's dispatch is the only thing
  that ever sets it.
- **The split.** `app.py` is the application — lifespan, CORS, the error body, `/health`. Routes
  moved to `services/core/routes/{session,interviews,patients}.py`, split by audience because the
  two audiences have **opposite** auth postures and a file that mixes them is a file where the
  next route lands on the wrong side by accident. The session routes stay unauthenticated: a
  patient never holds a credential.
- **`services/core/lifecycle.py`** — the call itself. `drain()` is called from `lifespan` and
  `teardown` / `_run` / `_speak_opening` / `_expire` are shared, so left in the session router
  they would have had to be imported back out of it by `app.py`. Both mints moved with it,
  because the ordering guarantee — assistant in the room before the patient holds a token — is a
  property of the call and not of a route.
- **Read routes, clinician only.** `GET /interviews` · `GET /interviews/{id}`, which carries the
  row, its `clinical.results` and `transcript.events` inline by `seq`, **including the safety
  scans that matched nothing** · `GET /patients`, scoped to the caller's own list plus the
  unowned demo rows and carrying `origin`. SQL in `services/core/reads.py`, models in
  `shared/contracts/models.py`, `make contracts` run.
- **Config.** `pyjwt[crypto]` and `httpx` became explicit dependencies — PyJWT was present only
  transitively through `livekit-api` and `cryptography` was not installed at all, so ES256 would
  have failed on the first token. `SUPABASE_URL` joined `config.py`; the issuer and the JWKS URL
  both derive from it, and it is required outside dev exactly as `DATABASE_URL` is. The pool grew
  from four connections to six: the dashboard's reads must not queue behind a transcript batch.
- **Tests.** `tests/test_auth.py` in plain `make test` — an EC keypair generated in a fixture and
  a JWKS served from memory, so the real ES256 path runs with no network and no project.
  Anonymous 401, expired 401, wrong issuer 401, wrong audience 401, `alg: none` 401, no `exp`
  401, forged signature 401, verified-but-unknown 403, wrong role 403, clinician 200 — and the
  identity arriving at the *query*, not just the door. `tests/test_reads.py` under the postgres
  marker holds the data half, including the one that matters: a clinician does not see another
  clinician's patient, and the interview it links to is 404 rather than 403.
- **`scripts/auth.sh`** — password grant against `/auth/v1/token`, print the claims, then `curl`
  a clinical route with the token and again without it. The dashboard's contract before the
  dashboard exists.

**Still outside the repo:** create the auth user in the Supabase console and match the seed row
in `20260825101500_accounts.sql` to its email; confirm the project is on asymmetric JWT signing
keys (new projects are; one created on the legacy secret is switched in Settings → JWT Keys).
Neither is a code change, and neither blocks `make check`.

**Done:** every clinical route refuses an anonymous caller; curl with a JWT returns the interview.

---

## 3 · Deploy — **next**

*Why now:* one deploy of something durable rather than two of something that evaporates — the
first public URL already keeps what it records and already refuses an anonymous reader. Both of
those are now true rather than planned.

- `Dockerfile` (uv, `--frozen`) + `fly.toml`: 1 vCPU / 1 GB, `min_machines_running = 1`, no volume.
- LiveKit Cloud → `LIVEKIT_PUBLIC_URL`.
- Fly secrets: `DATABASE_URL`, `SUPABASE_URL`, `METAFORA_ENV=prod`. No JWT secret — that is
  what choosing JWKS bought, and `config.py` refuses to import without the two that remain.
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
- **Attribute-based access control** — rules over relationships (clinician ↔ patient ↔ practice)
  instead of roles. Wanted by the first of these to arrive: a second practice on one deployment,
  the studio's split between safety leads and clinicians, or break-glass access with an audit
  trail. The natural mechanism with Supabase is Postgres RLS, and the caveat goes on the record
  now: Phase 1 gave `db.py` one pooled *service* connection, and RLS wants per-request identity on
  the connection (`set local`). That trade is to be made deliberately, not discovered.
- **`services/comms/`** — reminders, then telephony: an outbound call to a patient who never opens
  an email. This is the route the product is really for; the link is the stand-in.

Out of scope: a patient app with accounts, which is what photo capture would need.

---

`make contracts` after any change to `shared/contracts/models.py`. CI enforces it.
