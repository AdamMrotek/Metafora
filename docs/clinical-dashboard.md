# Clinician dashboard — `frontend/dashboard/`

The clinician-facing half of the product. **Built — the read path** (roadmap Phase 4):
`frontend/dashboard/`, a Vite app on `:5174` in dev, over `GET /interviews`,
`GET /interviews/{id}`, `GET /patients` and `GET /me`. What is described below
is the design spec `docs/ux/clinical-dashboard.html`, which is frozen; §*What is
real* at the bottom says which of it the app actually backs with a query. (The
wordmark in the spec, `threep.io`, is placeholder branding; the app says
`metafora.care`.)

## Who uses it

A named clinician, at a desk, signed in, coming back every day. The opposite of
the patient portal in every way that matters: an account, a panel of patients, a
history, and a signature that carries their name. Everything a patient says ends
up here.

## What it does

Three screens onto one patient record, plus a band that follows you across all
of them.

**The escalation band.** Full bleed, red, at the top of every screen: someone's
health is deteriorating and a consultation is owed. It carries the patient's own
words, a clock counting how long it has been owed, and one button. It is not a
notification — only a human clears it.

**Dashboard** — answers three questions, in this order, and the order *is* the
layout:

- Is anything on fire?
- What do I owe a decision on?
- Who is on my list?

A greeting with the count freshness, three stat tiles (review ready · in progress
· expiring within 48h), then the review table: patient, interview and how much of
it was captured, when it ended, the outcome in the patient's words, and a status
pill. Below it, scheduled care calls for the next seven days and an opt-in
patient-experience panel — counts by sentiment, not an average score, because an
average hides whether anyone answered at all.

**Interview detail** — what the arrow on a review row opens, and it opens
*inside the patient's context*: breadcrumb, care team, consent chips, and a
timeline of every previous interview with the gaps between them. Then two panes.
Left, the transcript, with every safety scan that ran shown against the turn it
ran on — including the ones that matched nothing, so you can see what was looked
for and cleared. Right, the review composer: a read-only issued summary, a
clinical impression you type, a disposition, and one irreversible **Sign** button
pinned to the record hash and ledger head.

**Patients** — search by NHS number, or name plus DOB, with your own list beside
it: last interview, next scheduled, how much is still open. Search a patient you
have no assignment to and you get identity and nothing else; taking them on asks
for a typed reason first.

**Deployments** — sending an interview is one decision made four times: *who,
what, when, how.* One patient at a time — no batch, no cohort, no list upload,
because a send that covers twelve people has stopped being a clinical decision.
Below the form, the upcoming calls, one row per call still to happen, columns
matching the four choices. Calls that have already come back belong to the
dashboard, not here.

## The shape of the work

Five steps: **deploy → intake → return → review → sign.** The questions are fixed
and approved long before any of it; nobody authors an interview per patient (that
is the studio's job, also unbuilt). What varies is how far it gets, and every
interview comes back in one of three states:

| | |
|---|---|
| **Urgent escalation** | Rare. The gate fires, the interview stops, the patient is told a clinician will contact them, a named clinician is alerted. |
| **Issue raised** | It did not complete, or a soft concern was raised — abandoned, verification locked. |
| **Review ready** | It ran to the end and the output is complete. Most interviews end here. |

## What is real

Everything the spec draws is on the screen. Not all of it is a query, and the
difference matters more than the screenshot does, so it is written down here
rather than discovered.

**Backed by the record.** The review table, its capture meter
(`captured_fields`/`total_fields`, counted in a lateral join over
`clinical.results`), every status and outcome sentence, the stat tiles, the
transcript — including **every** `safety.scanned`, the ones that matched nothing
as much as the one that fired — the captured fields, the patients list with
`origin` distinguishing a dispatched patient from a demo visitor, the history
timeline, and the escalation band, which counts and clocks real
`outcome = 'safety'` rows and quotes the patient's own words from the turn the
gate stopped on.

**Illustrative — `frontend/dashboard/src/demo.ts`, and nowhere else.** NHS
numbers, dates of birth, consent chips, the referral context in the issued
summary, the composer's record hash and ledger head, and the whole
patient-experience panel. This product has never collected demographics — a demo
visitor gives a first name — and nothing asks a patient how the interview went.
Each value is derived from a real id so it does not move between renders, and
the file is deletable in one commit. The chrome carries a `demo data` chip.

**Deliberately inert until Phase 5.** Clinical impression, disposition, Sign,
Add patient, the Deployments screen, and the band's own action. A control that
cannot honour what it offers is worse than one that plainly is not offering it
yet.

## What it still needs

- **A push channel for escalations** — the band claims seconds and currently
  learns about one when the page loads. Phase 5: `clinical.escalations` and an
  SSE stream from `svc-core`, scoped by the same `where` clause as the read
  routes.
- **Dispatch** — `POST /interviews`, `clinical.invitations`, and the emailed
  link. It is also the only thing that ever sets `clinician_email`, which is the
  column every scope in `reads.py` turns on; until it exists, a caseload is the
  unowned demo rows.
- **A signature ledger** — the composer's hash and ledger head imply
  append-only, tamper-evident storage that does not exist.
- **A patient profile screen** — the patients table's arrow opens that person's
  most recent interview, because there is nowhere else for it to go.

The design tokens live in `frontend/shared/tokens.css`, which all three surfaces
share; the clinical components are `frontend/dashboard/src/dashboard.css`, lifted
from the spec's `<style>` block verbatim.
