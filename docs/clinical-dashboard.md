# Clinician dashboard — `frontend/dashboard/`

The clinician-facing half of the product. **Not built yet** — `frontend/dashboard/`
is an empty directory, and this page describes the design spec
`docs/ux/clinical-dashboard.html`, which is frozen, not running code. (The
wordmark in that file, `threep.io`, is placeholder branding.)

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

## What it would need

Nothing on this screen exists in the backend yet. Today `services/core/store.py`
holds sessions in memory for the length of a call and `logs/<sessionId>.jsonl` is
the durable artefact; the dashboard wants the opposite — a persisted clinical
record, queryable by patient, surviving restarts. Specifically:

- **Accounts and roles** (`shared-auth` in `docs/system-map.md`) — the patient
  portal deliberately has none; this cannot work without them.
- **Patients, panels and assignment** — there is no patient store, only
  `QueuedInterview.patient` on one in-flight session.
- **Interview results, persisted and listable** — the review table is a query,
  and JSONL on disk is not one.
- **A push channel for escalations** — the band claims seconds, and
  `services/agent/safety.py` already produces the `ScanResult` it would carry.
- **A signature ledger** — the composer's hash and ledger head imply
  append-only, tamper-evident storage that does not exist.

The design tokens are in the spec's `<style>` block; `frontend/shared/` is
currently a `tokens.css` stub, and this is the app that would make it real.
