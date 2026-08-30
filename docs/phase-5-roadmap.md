# Phase 5 — dispatch, red flags, the signature ledger

## Context

Phase 4 shipped the clinician dashboard's **read** path. Everything the dashboard *writes* is
still unbuilt, and `docs/roadmap.md:299-341` calls that Phase 5: dispatch, escalations, sign-off.
Until dispatch exists, `clinical.patients.clinician_email` is never set, so "a caseload" is just
the unowned demo rows; until 5b, a red flag was counted on the row and nothing more — nobody
obliged to clear it; until the ledger exists, the composer's hashes come from
`frontend/dashboard/src/demo.ts`.

This document stages that into **5·0 identity → 5a dispatch → 5b red flags → 5b·1 the flag set →
5b·2 push → 5c ledger**, each independently shippable, in the order the MVP line needs them (deploy → intake →
return → review → sign). 5·0 is the odd one out and is **shipped**: it depends on none of the
others and was the cheapest way to shrink `demo.ts`, which every later stage otherwise inherits.

**5·0, 5a and 5b are shipped.** `clinical.patients.clinician_email` is written, so the scope
predicate in `reads.py` discriminates; `clinical.interviews.acknowledged_at` is the second such
predicate, and 5c is built on both.

**5b was three jobs and is now two, and the first of them is shipped.** The acknowledgement is 5b;
re-authoring the flag set and what the patient hears on a triage flag is 5b·1. Neither waits for
the other — 5b reads the flag's *action* out of the protocol version each interview pinned, so the
flags may move underneath it without moving a line already on the band.

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

# 5b · Red flags a human clears — **shipped**

**Done:** a red flag puts a line on the dashboard saying *what is owed*, and only a named clinician
clears it. Getting that line there without a reload is still 5b·2.

Everything below was built as specified. Four departures, recorded rather than quietly taken:

1. **`worst_flag` is now resolved through the pinned protocol version, not off the scan's
   payload.** This was not in the plan and is the only change to code 5b did not otherwise touch.
   `SafetyScanned.action` is the worst action of a whole *turn*, so on a turn matching a yellow and
   a red every hit inherits the red's rank — which is fine for a per-call maximum and wrong for a
   band that has to name *which* flag. Once the band resolved each hit against the version, the two
   consumers of §2's one predicate were reading two different things again, and the scratch
   database proved it: `test_the_tiles_count_the_whole_scope_not_a_page` came back `urgent=11` with
   ten lines in the band, the eleventh being a fabricated hit id no protocol defines. So
   `_SUMMARY_FROM`'s lateral now left-joins `pr.version -> 'redFlags'` and ranks the flag's own
   action. `flag_count` is deliberately unchanged — an unrecognised hit still counts, because
   something did match.
2. **No partial index on `acknowledged_at is null`.** The plan asked for one; that predicate
   matches nearly every interview ever recorded, so it selects nothing and costs a write per insert
   to say so. What narrows the band is the flag, which is in `transcript.events`. The index that
   shipped is `interviews_acknowledged_by`, partial on `acknowledged_by is not null` — the one the
   foreign key wants, on `patients_by_clinician`'s pattern.
3. **`Overview.escalations` is still on `/overview`, not a route of its own.** The plan's "a shared
   fetch in `data.tsx` beside `/overview` and `/patients`" is satisfied by the field that was
   already there: the band, the `urgent` tile and the review table's row all move on the same fact,
   so one refetch is what makes them agree rather than three that can land out of order.
4. **`reads.RED_ACTIONS` is derived from `SEVERITY`, not typed out.** `('end_call',
   'urgent_escalate')` is everything ranked at or above a triage escalation, read from
   `safety.py`'s own dict — so 5b·1 can move flags between actions without editing `reads.py`.

**The acknowledgement is one statement.** `update … set acknowledged_at = coalesce(acknowledged_at,
now()), acknowledged_by = coalesce(acknowledged_by, $1) … where {OWNED_BY}`. Idempotence,
non-reassignment and the scope check are all in that one `where`/`coalesce` pair, which is what
makes them hold under two clicks at once — the shape "select, decide, update" has a window between
the first two where both callers see null. `NotFound` when it matches nothing, exactly as a read
outside scope answers.

**Not touched, and worth knowing:** `PatientSummary.has_escalation` on the patients screen is still
`bool_or(i.outcome = 'safety')`. It answers a different question — *what is still owed on this
person*, alongside `open_count` — and the band's predicate is per interview. It is the third place
in the repo using the word, and the one to look at first if the patients table and the band ever
disagree.

**Original plan, as built:**

## Scope: the acknowledgement, and nothing else

An earlier draft of this stage did three jobs at once — rewrite the flag set, give the agent a new
sentence to speak on a triage flag, and build the acknowledgement. Only the third matches the
sentence above, and the first two change what the *patient* hears, which wants a different kind of
review than a schema change. They are **5b·1**, below, and nothing here waits for them.

Nothing needs authoring first. `rf_anticoagulant_taken` and `rf_fitness_change` are already
`action='urgent_escalate'` (`services/agent/config/protocol.py:273,286`), so the record already
holds reds this stage can put on the screen and clear. Today they reach no band at all — the band
is `i.outcome = 'safety'` (`reads.py:337`), which only ever catches the `end_call` path.

## No escalations table, and no write at flag time

`PostgresSessionWriter.append` already queues `safety.scanned` to `transcript.events` off the media
path (`session_log.py:255-267`), and `reads.py` already derives the worst flag from those rows. The
one thing the record cannot produce is **that a human has seen it**.

### 1 · Migration

`acknowledged_at` and `acknowledged_by → config.accounts(email)` on `clinical.interviews`, partial
index on `acknowledged_at is null`. No new table, no `escalations.py`, no gate callback. No fifth
`status`: "completed and also flagged" is `status='completed'` with `acknowledged_at` null, which
leaves the CHECK and `claim()` alone.

### 2 · One predicate, defined once

Red is the flag's **action**, never the id prefix:

```
worst_flag in ('end_call', 'urgent_escalate') and i.acknowledged_at is null
```

It lives beside `OWNED_BY` in `reads.py` and both consumers read it: the band *and* the `urgent`
tile. Today the tile is `i.outcome = 'safety'` (`reads.py:303`) while the band would be the new
predicate — two numbers about the same thing on the same screen, which is invariant 4's failure in
a new dress, and the band's own sentence quotes the tile's number (`App.tsx:114-117`). So `urgent`
changes meaning with it: **reds not yet acknowledged**.

**Any status counts, including `running`.** An `end_call` red lands `abandoned` within seconds, so
this only bites the triage kind: the flag exists the moment the gate scans, and the call runs on
for minutes. The clock starts at the scan, not the hangup — and a band that showed only ended calls
would leave 5b·2 with nothing live to push.

### 3 · A read shaped like the line it draws

The band's line needs the flag's **label** and the **time it fired**. `InterviewSummary` has
neither: `worst_flag` is the action string (`reads.py:186-188`), and there is no scan timestamp on
the row at all. So a new `reads.escalations(user)`, scoped by `OWNED_BY` verbatim, returning
`interviewId · patientFirstName · flagLabel · action · raisedAt · dueAt` — resolving the hit id to
its label by joining `config.protocols.version`, and computing `dueAt` as `raisedAt +
urgent.timeout_minutes` **from the version the interview pinned**, so re-authoring a timeout never
retro-moves an old deadline. `Overview.escalations` stops being `InterviewSummary[]`.

Not by widening `_SUMMARY_COLUMNS`: the band is a worklist line, not a table row, and the review
table should not carry two columns only a banner reads. It also retires `useFlaggedWords`
(`App.tsx:135`), which reconstructs the patient's words from the transcript in the browser to say
what the label says better.

### 4 · Write

`POST /interviews/{id}/acknowledge` in `routes/interviews.py`, `ClinicalReader`, scoped by
`OWNED_BY`. It means *I have this* and nothing more — no `close_reason`, no disposition. What was
decided belongs to the sign-off or to the practice's own systems.

**Per interview, not per flag,** so a call carrying two reds is cleared once: "I have this" is a
statement about the call, not about a pattern match — and per-flag would need the table this stage
refuses. **Idempotent and not reversible:** a second POST returns the first stamp and the first
`acknowledged_by` rather than overwriting them, so it cannot quietly reassign who owns it, and
there is no un-acknowledge because nobody has yet needed one.

### 5 · Dashboard

`Rail` (`App.tsx:78-120`) reads `escalations` instead of filtering `outcome === 'safety'`; the
inert **Schedule consultation** button becomes acknowledge. A shared fetch in `data.tsx` beside
`/overview` and `/patients`, because it describes the whole caseload (invariant 4) — so it arrives
on load and after an acknowledgement, and no sooner.

**The band names what is owed, not how bad it is:**

> **Sarah Chen** · Anticoagulant not stopped as instructed · decision owed by 17:00

The two reds ask different things — `end_call` is *this call was stopped, make contact*,
`urgent_escalate` is *a decision is owed by «time»* — and are distinguishable from the `action` the
read already returns, with no new field.

### 6 · Tests

A yellow raises no band; a red appears for its owner and not for a stranger; a red on a `running`
interview is in the band; acknowledge is scoped, idempotent, and removes it from both the band and
the `urgent` tile; a second acknowledge by a different clinician does not change `acknowledged_by`.

**Nobody is paged.** The band reaches an open dashboard; the honest description is "it will be
seen", not "someone will call" — 5a's dropped email channel again. So no `notified_at`, which would
claim an event nothing produces, and no `rota_target`: the rota is on the protocol version the
interview ran (`protocol.py:330`), versioned and append-only, which is where a notifier will read
it.

**Verify:** take a call and say *"I'm still taking the apixaban"* — `rf_anticoagulant_taken`'s own
`proving_utterance`, a red in the protocol as it ships today. Reload: the band names the decision
and its deadline, and clears only when acknowledged. A second clinician never saw it. An attendance
answer produces a yellow row and no band.

---

# 5b·1 · The flag set re-read

**Done when:** the four actions are named for what they ask of a human, the triage red catches
something worth catching, and the rule that keeps the flag set from growing into symptom triage is
written down in `safety.py`.

Split from 5b because none of it is needed to clear a flag, and all of it changes what a patient
hears. Order between them is free; 5b is written against the protocol as it ships today, and this
stage moves flags underneath a band that reads the action rather than the id.

## Red and yellow are the four actions, renamed

Not a new axis to store — a name for the grouping `SEVERITY` already makes, 1:1:

| | action | the call | the patient hears | who acts, and by when |
|---|---|---|---|---|
| **Red · emergency** | `end_call` | stops | 999 / Samaritans, **and** that the practice will contact them | the practice, today. Custodial, not clinical |
| **Red · triage** | `urgent_escalate` | continues | nothing authored — see below | a clinician, before the list |
| **Yellow** | `soft_review` | continues | nothing authored | the unit — **proceed / move / cancel** |
| **Note** | `note_only` | continues | nothing | context in the record |

**`rf_fitness_change` and `rf_anticoagulant_taken` move to `soft_review`**, renamed `yf_`. They are
urgent to the *booking*, not to the patient. Yellow is then one thing: those two plus
`yf_attendance_risk` and `yf_no_escort` are all *the unit has a decision to make about this
booking*. Every call produces yellows; reds are rare, which is what makes a band that is usually
absent worth reading.

**The triage red has to be authored.** Once those two move, `urgent_escalate` is empty — and
nothing in `PREOP_CHECK_V1` catches *new pain where they are about to operate*, which is what
`PREOP_CHECK_V1.urgent.timeout_minutes = 120` is the clock for. Its patterns must be phrases worth
a look out of context — `new pain`, `getting worse`, `red and hot` — never `pain`. `safety.py`
handles no negation, so false positives are certain, and the rule that makes them acceptable
belongs in that file:

> **A red flag is legitimate only when the correct response to a false positive is still
> acceptable.** Stopping an admin call so a person rings back is. Producing clinical advice is not,
> at any accuracy.

That is what stops the flag set growing into symptom triage.

## The patient hears nothing new, and that is decided

An earlier draft allowed `say` on `urgent_escalate`, spoken alongside the generated reply. It is
dropped, on a mechanical objection rather than an editorial one: the gate can only speak by pushing
a `TTSSpeakFrame` (`gate.py:83`), and that is safe on the blocked path *because* the transcript
frame is swallowed there and nothing else is generating. On a triage flag the turn goes on to the
model, so the gate's sentence and the generated reply race into the same TTS — two utterances with
no defined order, under barge-in constants (`config/tuning.py`) set for one.

The authored-sentence rule stands for the reds that do stop the call — every one, on `SELF_HARM`'s
model:

> **[what this system is doing] + [a route that does not depend on this system]**

Nothing in this deployment pages anybody, so *"a clinician will call you"* is a promise it cannot
keep. If a triage sentence is ever wanted, it needs the pipeline to guarantee it precedes the
generated turn — a real change to `pipeline.py`, and its own decision.

**Tests:** the moved flags scan `soft_review`; the new triage red scans `urgent_escalate` on its
`proving_utterance`; `test_prompts.py`'s line holds; the band from 5b follows the flags without
changing, because it reads the action.

**Verify:** an apixaban answer now produces a yellow row and no band, where before it produced a
band. The new triage red produces one.

---

# 5b·2 · Live push

**Done when:** a red flag raised on a call in progress reaches an already-open dashboard in seconds.

Split from 5b because it is the only part with a long-lived connection — a stream, a reconnect
strategy, a broadcaster with a lifetime, and a limit on how many machines this may run on. It is
also the only part that needs the gate to report upward: 5b's flag reaches Postgres on the
`SessionWriter` the pipeline already holds, so `gate.py`'s report and its wiring in `lifecycle.py`
(beside `_on_blocked`, `:54-64`) belong here.

1. **Broadcaster** — in-process `asyncio` fan-out. `publish(interview_id)` from the gate's report,
   per-connection subscriber queues, bounded, dropping a slow subscriber rather than backing the
   publisher up: a call must never feel a browser. Drained in `app.py`'s `lifespan`.
2. **Route** — `GET /interviews/stream`, SSE behind `ClinicalReader`, filtered per subscriber by
   `OWNED_BY`. Heartbeat comment every ~15s so a proxy does not close it.
3. **A nudge, not a payload** — the event carries an interview id; the browser refetches the band it
   already fetches. A lost message then costs seconds rather than a wrong band, the stream cannot
   disagree with the query, and no medical text crosses it.
4. **Dashboard** — subscribe beside `data.tsx`'s band fetch and call its `reload()`. *Trap:*
   `EventSource` cannot send an `Authorization` header — use `fetch` with a `ReadableStream` reader
   and backoff reconnect. **The fallback is 5b**: stream down means the band is what the last fetch
   returned, degraded rather than broken.
5. **Not Supabase Realtime** — `docs/system-map.md:204-211`, `docs/roadmap.md:325-335`. In-process
   fan-out means **one machine**, which `docs/deployment.md` assumes; read `fly.toml` against that
   before it ships. A nudge-shaped event is the cheapest thing to move onto a real bus later.
6. **Tests** — delivers to the owner and not to a stranger; an unauthenticated subscriber is
   refused; a dropped subscriber does not block the publisher; shutdown closes open streams.

**Verify:** two clinicians, two browsers. Drive a call into a red — the owner's band appears
untouched, the stranger's does not. Stop the backend: the owner's dashboard degrades to 5b and
reconnects on its own.

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

5·0, 5a and 5b are shipped. 5a was the only stage that makes `clinician_email` non-null, and every
remaining stage scopes on it — so 5c can be built whenever, subject to the ordering caveat recorded
under it.

5b·1 is free of 5b in both directions. It is the only stage that changes the call itself, and 5b is
written against the protocol as it ships today, so neither blocks the other.

5b·2 needs 5b, and nothing needs 5b·2 — it can be skipped indefinitely, because the band on load is
the fallback the push degrades to.

5a inherits one simplification from 5·0: the Deployments composer's patient select can show an NHS
number, because the patients it lists have one.
