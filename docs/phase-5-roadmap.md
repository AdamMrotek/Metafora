# Phase 5 — dispatch, escalations, the signature ledger

## Context

Phase 4 shipped the clinician dashboard's **read** path. Everything the dashboard *writes* is
still unbuilt, and `docs/roadmap.md:299-341` calls that Phase 5: dispatch, escalations, sign-off.
Until dispatch exists, `clinical.patients.clinician_email` is never set, so "a caseload" is just
the unowned demo rows; until escalations exist, an `urgent_escalate` red flag is counted on the row
and nothing more — no record, no notification, nobody obliged to clear it; until the ledger exists,
the composer's hashes come from `frontend/dashboard/src/demo.ts`.

This document stages that into **5·0 identity → 5a dispatch → 5b escalations → 5c ledger**, each
independently shippable, in the order the MVP line needs them (deploy → intake → return → review →
sign). 5·0 is the odd one out and is **shipped**: it depends on none of the others and was the
cheapest way to shrink `demo.ts`, which every later stage otherwise keeps inheriting.

**5·0 and 5a are both shipped.** `clinical.patients.clinician_email` is now written, so the scope
predicate in `reads.py` discriminates for the first time and 5b and 5c can be built on it.

**5a deliberately drops the email channel.** The roadmap's dispatch sends an emailed link 24h
before `scheduled_for`; an email provider is still unchosen and would be a fourth egress
(`docs/roadmap-review.md:175-179`). Instead the clinician copies the link from the dashboard and
sends it however they like. Everything else about dispatch — the token, the invitation record, the
`queued` row, the claim — is built for real, so adding `services/comms/` later is a sender, not a
redesign.

---

## What already exists (do not rebuild)

- `services/core/queue.py:64` — `resolve_interview(token: str | None = None)` **already takes a
  token**; `_by_invitation` (`:90`) is a stub raising `UnknownInvitation`.
- `services/core/queue.py:98` — `claim(interview_id)` does `queued → running` with `for update
  skip locked`, already tested. Both arrival paths end here.
- `clinical.interviews.scheduled_for`, the partial index `interviews_queued`,
  `clinical.patients.origin='dispatched'` and `clinical.patients.clinician_email` are all in the
  schema and unwritten.
- `reads.py:30` — `OWNED_BY = "(p.clinician_email is null or p.clinician_email = $1)"`, the one
  scope predicate. Every new read reuses it verbatim.
- `shared/auth/deps.py:134` — `ClinicalReader`. New write routes go in the existing routers so
  they inherit the router-level `require_role`, and take `ClinicalReader` as a parameter.
- `services/core/store.py:101` `_record_outcome` is the model for a Postgres write from core.
- Append-only precedent: `config.reject_mutation()` / `transcript.reject_mutation()` in
  `supabase/migrations/20260824122946_initial_schema.sql:29-40,106-117`.

---

# 5·0 · Seeded identity — **shipped**

**Done:** NHS number, date of birth and the patient-experience chart are rows the dashboard
queries, not values the browser invents.

The problem was not that those values were made up — this is a demo, and they have to come from
somewhere. It was *where*: `demo.ts` drew them in the browser from an FNV hash of the patient id,
so `NHS ···· 447` rendered in the same `.sub.mono` style as the real timestamps beside it, behind
one app-level chip that said "some of what is drawn is illustrative" without saying which. The
answer was to make them real queried values that are **synthetic by construction**.

| | |
|---|---|
| Numbers come from | 999 000 0000 – 999 999 9999, the range NHS England reserves for test data, each with a valid Modulus 11 check digit |
| Enforced by | `patients_nhs_number_is_test`, a CHECK on `clinical.patients`. Not a convention — a deployment of this repo **cannot** hold a real identifier, and a clinical one drops one line to change that |
| The demo caseload is | ten seeded patients. A visitor to the public demo takes a call *as* one of them rather than becoming an eleventh person |
| Still invented | `referral`, `consent`, `hashes` — see below |

**What shipped**

- `supabase/migrations/20260829094500_patient_identity.sql` — `nhs_number` / `date_of_birth`
  columns, the CHECK, a partial unique index, and the ten-row roster.
- `supabase/migrations/20260829094600_experience.sql` — `metrics.experience_responses`, the first
  table in that schema, seeded with a fortnight of sentiments.
- `services/core/queue.py` — `ROSTER_PICK`. The tokenless path selects a roster patient and inserts
  only the interview. A seeded identity *is* what defines a roster row (`nhs_number is not null`),
  so the per-visitor rows an older deployment already minted keep their history and stop growing.
  The no-database mint is untouched.
- `services/core/reads.py` — both summaries carry the identity; `experience(user, range)` is a new
  read scoped by the same `OWNED_BY`, joined through the patient. `services/core/routes/experience.py`
  is `GET /experience`, behind the router's `require_role` like the other three.
- `frontend/dashboard/src/format.ts` — `nhsMasked` and `dob` moved here, because they now format
  something a query produced. `demo.ts` lost `nhsNumber`, `dateOfBirth` and `experience`.

**Two decisions worth keeping**

*The experience window anchors on `max(responded_at)`, not `now()`.* Nothing writes that table — the
rows are a seed — so a clock-anchored window draws an empty chart the day the deployment outlives
its own migration. The caption (`scope`) names the fortnight instead of saying "today", which is
what keeps the anchoring honest rather than merely convenient.

*Interviews are not seeded.* Every interview on the dashboard is a call that actually happened. A
fresh database shows ten patients with no history until someone takes a demo call, and that is the
honest reading of an empty review table.

**What `demo.ts` still holds, and why**

- `referral` — the protocol is recorded; what it is *for* is not.
- `consent` — deliberately **not** seeded. `docs/system-map.md:347-350`: claiming a consent record
  you do not hold is worse than holding none, and a seeded one would be exactly that. It goes when
  a consent record exists, or when the chip goes.
- `hashes` — 5c's to delete.

The `demo data` chip now names those three rather than gesturing at the screen.

---

# 5a · Dispatch (no email) — **shipped**

**Done:** a clinician creates an interview from the Deployments screen, copies its link, opens it
in the patient portal, and that call runs against *that* interview — with the row's owner set.

Everything below was built as specified. Five departures from the plan, all recorded rather than
quietly taken:

1. **Ownership is not claimed on an existing patient.** Only a newly created one gets
   `clinician_email`. Dispatching a call to one of the shared demo roster would otherwise take that
   person off every other clinician's dashboard, and being sent a call by somebody does not make a
   person theirs retrospectively. `tests/test_dispatch.py::test_dispatching_to_a_demo_patient_does_not_claim_them`.
2. **`invitations.resolve` is `invitations.spend`,** and it is one conditional `UPDATE … RETURNING`
   rather than a select then a stamp. Two tabs opened from one link cannot both find it unspent;
   the plan's shape could.
3. **`GET /protocols` was added,** the roadmap's own first option. Deriving the composer's *what*
   field from the ids on existing summaries would leave it empty on a fresh database, which is
   exactly when it is needed.
4. **`reads.summary(user, id)` was split out of `reads.interview`,** so `dispatch.py` answers with
   the row the review table draws rather than assembling a second one.
5. **`.dep` / `.send` were lifted from the spec's stylesheet** into the region *below* the APP SHELL
   banner. They are the spec's own rules verbatim — the one block left behind when the rest was
   moved, because no screen used it — but they sit below the banner so that "everything above is the
   spec, in its own order" stays true of that region.

Two things the plan named that are not there: `sent_at` is written by nothing, because handing a URL
to a clinician is not the same event as it reaching a patient and the column should not claim it is;
and `GUARDED` in `tests/test_auth.py` became `(method, path)` pairs, because a list of paths could
not hold the two routes that *write*.

**Original plan, as built:**

**Done when:** a clinician creates an interview from the dashboard, copies its link, opens it in
the patient portal, and that call runs against *that* interview — with the row's owner set, so
the caseload scope in `reads.py` finally means something.

## Decisions taken

| | |
|---|---|
| Link carries | an **opaque invite token**, never the interview id |
| Link assembled by | the **backend**; `POST /interviews/{id}/invitation` returns the complete URL. No `VITE_` var — consistent with `frontend/dashboard/src/auth/supabase.ts:7`, which argues the bundle holds no configuration |
| Copy button lives on | the **Scheduled care calls** card (`Dashboard.tsx:282-320`), the only rows for which a link is meaningful — the link is spent when the call starts |
| Second click | **reuses the live invitation** if it is unspent, otherwise mints a fresh one |
| Channel | `'link'` only. The column exists for `'email'` later |

### How reuse works without storing a token in plaintext

The roadmap says the token is stored hashed. A hash cannot be un-hashed, so "reuse the same link"
normally forces a re-mint on every click — which silently kills a link already sent to a patient.
Resolve it by **deriving** the token instead of drawing it randomly:

```
token = base64url( HMAC-SHA256(INVITE_SECRET, f"{interview_id}:{nonce}") )   # 43 chars
row   = (interview_id, nonce, token_hash = sha256(token), channel, ...)
```

`nonce` is stored in the clear (it is not a secret); the token itself is stored only as a hash, so
a database leak still yields no working link. Re-deriving from the same row gives the same link →
reuse. Rotating = insert a row with a new nonce and set `revoked_at` on the old one. A changed
`INVITE_SECRET` invalidates every outstanding link, which is the correct behaviour and is checked
by re-hashing before returning a reused link.

## Backend

**Migration** — `supabase/migrations/<ts>_invitations.sql`

```sql
create table clinical.invitations (
  id            text primary key,
  interview_id  text not null references clinical.interviews (id),
  nonce         text not null,
  token_hash    text not null unique,
  channel       text not null default 'link' check (channel in ('link','email')),
  created_at    timestamptz not null default now(),
  sent_at       timestamptz,
  expires_at    timestamptz,
  opened_at     timestamptz,
  revoked_at    timestamptz
);
create unique index invitations_live on clinical.invitations (interview_id)
  where revoked_at is null and opened_at is null;
create index invitations_by_interview on clinical.invitations (interview_id, created_at desc);
```

Not append-only: `opened_at` and `revoked_at` are updates by design.

**`services/core/invitations.py`** (new) — `mint(pool, interview_id) -> Invitation`,
`resolve(pool, token) -> row | None`, `_derive`, `_hash`. This module and `dispatch.py` become
writers; update the "store.py and queue.py own every write" docstring at `reads.py:10-11`.

**`services/core/dispatch.py`** (new) — `create_interview(user, request) -> InterviewSummary`.
In one transaction: resolve or insert the patient (`origin='dispatched'`,
`clinician_email=user.email`), refuse a patient outside `OWNED_BY` with `NotFound`, insert the
interview `status='queued'` with `scheduled_for`, return the summary via the existing
`_SUMMARY_COLUMNS`/`_SUMMARY_FROM` in `reads.py` so one shape is produced in one place.

**`services/core/queue.py`** — implement `_by_invitation(token)`: look the hash up, refuse when
absent / revoked / expired / already opened, stamp `opened_at`, then `await claim(interview.id)`
and raise `UnknownInvitation` if the claim fails (the call already started — the link is spent).

**`services/core/routes/interviews.py`** — two POSTs, inheriting the router's guard:

- `POST /interviews` → body `DispatchRequest`, returns `InterviewSummary`
- `POST /interviews/{id}/invitation` → returns `Invitation` (`url`, `expiresAt`, `interviewId`)

**`services/core/routes/session.py:51`** — accept an optional body:
`body: SessionStart | None = None` (all fields defaulted, so today's body-less POST and
`tests/asgi.py`'s `start()` keep working). Pass `body.invite if body else None` into
`resolve_interview`. Catch `UnknownInvitation` → `HTTPException(404, "this link has expired or
has already been used")`; the frontend renders the sentence, per the roadmap's "a sentence on
screen, not a status code". Refuse a tokenless start with a sentence when `ALLOW_DEMO_SESSIONS`
is off (default on, per `docs/roadmap.md:317-321`).

**`services/core/config.py`** — `PORTAL_URL` (dev default `http://localhost:5173`),
`INVITE_SECRET`, `ALLOW_DEMO_SESSIONS`. `PORTAL_URL` and `INVITE_SECRET` join the
refuse-to-boot list outside dev, beside `DATABASE_URL`/`SUPABASE_URL`. Add all three to
`.env.example`.

**`shared/contracts/models.py`** — `DispatchRequest`, `Invitation`, `SessionStart`,
`InvitationChannel`. Then `make contracts` (CI fails without it).

## Frontend

**`frontend/dashboard/src/api.ts`** — add `post<T>(path, body)`, mirroring `get`'s
token-per-request shape plus `Content-Type: application/json` and the same `.error` unwrapping.

**`frontend/dashboard/src/CopyLink.tsx`** (new) — an icon button. Inline SVG in the house style
(`24 24` viewBox, `stroke="currentColor"`, `strokeWidth="1.8"`, round caps, `aria-hidden`), as
used at `frontend/call/src/App.tsx:84`. On click: `post` the mint, `navigator.clipboard
.writeText(url)`, show a `.tip`-derived "Copied" for ~1.5s with `aria-live="polite"`; call
`stopPropagation` so it is safe to reuse inside the click-to-navigate `<tr>` later. Fall back to
a selectable read-only input holding the URL when the clipboard API is unavailable (insecure
context).

**`frontend/dashboard/src/screens/Dashboard.tsx`** — render `<CopyLink interviewId={row.id} />`
in each `Scheduled` card item.

**`frontend/dashboard/src/screens/Deployments.tsx`** (new) — the spec's compose panel
(`docs/ux/clinical-dashboard.html:1290-1312`): *who · what · when · how*. Patient is a select
over the existing `/patients` list plus a "new patient, first name only" option — this product
collects no demographics and `docs/system-map.md:347-350` forbids inventing any. Protocol from a
new `GET /protocols` or the ids already on the summaries. Delivery is fixed to **Link**, with the
send row saying so plainly. On success: `reload()` from `data.tsx`, then show the minted link with
the same copy button.

**`frontend/dashboard/src/router.tsx`** — add `{ name: 'deployments' }` / `/deployments`;
`App.tsx:52-56` turns the inert `Deployments` anchor into a real `Link`.

**`frontend/dashboard/src/dashboard.css`** — new rules go **below line 349** (the APP SHELL
banner); everything above is `docs/ux/clinical-dashboard.html`'s stylesheet moved verbatim. Add
`.icon-btn` and the copied-confirmation, and add the new transition to the
`prefers-reduced-motion` list at line 331.

**`frontend/call/src/`** — read `new URLSearchParams(location.search).get('invite')` and send it
in the `POST /api/session` body (`useCall.ts:143`). A query param needs **no** SPA rewrite, which
`frontend/call/vercel.json` does not have. On a refusal, `Opening` (`App.tsx:68-113`) renders the
server's sentence instead of the Start button.

## Tests

- `tests/test_invitations.py` (`postgres`) — mint returns a URL; a second mint returns the *same*
  URL; a rotated secret does not; a token resolves to its interview and claims it; a second use is
  refused; revoked / expired / unknown are refused.
- `tests/test_dispatch.py` (`postgres`) — the row lands `queued` with `origin='dispatched'` and
  `clinician_email` set; it appears in `reads.interviews` for its owner and not for another
  clinician (mirror the scoping tests at `tests/test_reads.py:259-325`).
- `tests/test_auth.py:34` — add `/interviews` POST and `/interviews/{id}/invitation` to `GUARDED`.
- `tests/test_queue.py:42` — replace `test_a_link_is_refused_until_links_are_issued` with an
  unknown-token refusal that needs no database.
- `tests/test_schema.py` — the new constraints and the partial unique index.

## Verify 5a

1. `make check` and `make test-pg`.
2. `make dev`; sign in at `:5174`; **Deployments** → send an interview to a new patient.
3. The **Scheduled care calls** card shows it. Click copy; paste into a browser at `:5173`.
4. The portal starts a call **against that interview** — confirm the dashboard row moves
   `queued → running` and the transcript lands under that `interview_id`, not a fresh synthetic one.
5. Reopen the same link: refused with a sentence. Copy again before starting: same URL back.
6. `:5173` with no `?invite=` still starts a demo call (`ALLOW_DEMO_SESSIONS` on).

---

# 5b · Escalations

**Done when:** an `urgent_escalate` red flag writes a record, reaches an open dashboard in
seconds through `svc-core`, and only a human clears it.

Today `services/agent/safety.py:76` sets `blocked` for `end_call` **only**, so an
`urgent_escalate` produces a `safety.scanned` transcript row and nothing else
(`services/agent/gate.py:60-62`). `reads.py` now counts those rows onto the summary
(`flag_count` / `worst_flag`), so the review table and its tiles can tell a flagged call from a
clean one — but a count on a row is not an escalation: nothing is raised, nothing is notified, and
no one is obliged to clear it. The dashboard band still fakes it off `outcome === 'safety'`
(`App.tsx:88-91`).

1. **Migration** — `clinical.escalations (id, interview_id, patient_id, kind check in
   ('urgent','soft'), quote, say, rota_target, raised_at, notified_at, acknowledged_at,
   acknowledged_by → config.accounts(email), closed_at, closed_by, close_reason)`. Partial index
   on `closed_at is null`.
   *Recommendation:* do **not** add a fifth `clinical.interviews.status`. The roadmap's "fourth
   outcome state — completed and also an escalation" falls out for free as `status='completed'`
   with an open escalation row, and leaves the existing CHECK and `claim()` untouched.
2. **Report upward, write in core** (`docs/system-map.md:64`, rule 2). Give
   `services/agent/gate.py` an `on_escalation` callback alongside the existing blocked path;
   `services/core/lifecycle.py` wires it (next to `_on_blocked`, `:54-64`) and a new
   `services/core/escalations.py` does the insert.
3. **Authored acknowledgement** — allow `say` on `urgent_escalate` in
   `shared/contracts/models.py` and `services/agent/config/protocol.py`, spoken alongside the
   generated reply (`docs/example-interview.md:142-146`).
4. **Reads** — `reads.escalations(user)` using the same `OWNED_BY`; `GET /escalations`.
5. **Writes** — `POST /escalations/{id}/acknowledge` and `/close`, both `ClinicalReader`, both
   scoped by `OWNED_BY` so a clinician cannot clear someone else's.
6. **Push** — `GET /escalations/stream`, SSE held open from `svc-core`, behind `ClinicalReader`,
   filtered per subscriber by the same predicate. An in-process `asyncio` broadcaster fed by
   step 2, heartbeat comment every ~15s, drained in `app.py`'s `lifespan`.
   Explicitly **not** Supabase Realtime — `docs/system-map.md:204-211` and
   `docs/roadmap.md:325-335`. Record the constraint this carries: in-process fan-out means one
   machine, which `docs/deployment.md` already assumes.
7. **Dashboard** — `Rail` (`App.tsx:78-120`) reads the escalations list instead of filtering
   `outcome === 'safety'`; the inert **Schedule** button becomes acknowledge/close.
   *Trap:* `EventSource` cannot send an `Authorization` header. Subscribe with `fetch` + a
   `ReadableStream` reader, with backoff reconnect and a poll fallback.
8. **Tests** — a soft flag does not end the call and pages nobody; an urgent flag writes a row;
   scoping on read, acknowledge and close; the stream delivers to the owner and not to a stranger.

**Verify:** drive a call into an `urgent_escalate` flag with the dashboard already open; the band
appears without a reload, clocks correctly, and clears only when acknowledged — then confirm a
second clinician's dashboard never saw it.

---

# 5c · Signature ledger

**Done when:** a named clinician signs one interview irreversibly, pinned to the record's hash and
the ledger head, and the chain verifies.

*Ordering caveat, recorded rather than argued away:* `docs/roadmap.md:350-354` and
`docs/roadmap-review.md:161-169` both say mid-call resilience should probably land **before**
this — "the hash chain is more work and demonstrates less." 5c is staged last for that reason.

1. **Migration** — `clinical.signatures (id, interview_id unique, prev_hash unique, record_hash,
   hash, issued_summary, impression, disposition, signed_by → config.accounts(email), signed_at)`,
   with an append-only trigger reusing `reject_mutation()`. `interview_id unique` = one signature
   per interview; `prev_hash unique` = the chain cannot fork.
2. **`services/core/ledger.py`** — canonical serialisation of the record (the interview row, the
   results ordered by `field_key`, the transcript events ordered by `seq`) → `record_hash`;
   `hash = sha256(prev_hash ‖ record_hash ‖ impression ‖ disposition ‖ signed_by ‖ signed_at)`.
   Take the head under `select … for update` inside the insert's transaction so two concurrent
   signs serialise instead of forking.
3. **Routes** — `POST /interviews/{id}/signature` (409 if already signed) and
   `GET /interviews/{id}/ledger`, both scoped.
4. **Contracts** — `Signature`, `SignatureRequest`, a `Disposition` literal set;
   `InterviewDetail` gains `signature: Signature | None`.
5. **Dashboard** — the composer's impression and disposition become live inputs, `Sign as <name>`
   posts, the `record … · ledger head …` line reads real hashes, and the spec's **Ledger** tab
   (`docs/ux/clinical-dashboard.html:1106`) renders the chain. Timeline `signed` pills read from
   the record.
6. **`demo.ts`** — delete `hashes`. 5·0 answered most of what this step used to defer:
   `nhsNumber`, `dateOfBirth` and `experience` are seeded rows now, and the file is already
   narrowed to an honest docstring. What is left after `hashes` goes is `referral` and `consent`,
   and `consent` is a deliberate hold rather than an oversight (`docs/system-map.md:347-350`). The
   `demo data` chip stays until both do.
7. **Tests** — the chain verifies end to end; a tampered record fails verification; a double sign
   is refused; concurrent signs do not fork; an out-of-scope interview cannot be signed; the
   append-only trigger refuses update and delete (`pytest.raises(Exception, match="append-only")`,
   as in `tests/test_schema.py:58-85`).

**Verify:** the full MVP line on the deployed URL — dispatch a call, take it as the patient,
review it, sign it, then re-read `GET /interviews/{id}/ledger` and confirm the head matches.

---

## Order of work

5·0 and 5a are both shipped. 5a was the only stage that makes `clinician_email` non-null, and both
remaining stages scope on it — so 5b and 5c can now be built in either order, subject to the
ordering caveat recorded under 5c.

5a inherits one simplification from 5·0: the Deployments composer's patient select can show an NHS
number, because the patients it lists have one.
