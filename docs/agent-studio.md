# Agent studio — `frontend/studio/`

The build-side half of the product. **Not built yet** — `frontend/studio/` does
not exist. The design spec `docs/ux/agent-studio.html` is much bigger than this;
what follows is the scope we actually want first. See
[`next-features-overview.md`](./next-features-overview.md) for the open decisions.

## Who uses it

Whoever writes the interview — a clinician or clinical-safety lead, at a desk,
before any patient is involved. They are authoring config, not talking to
anyone. Nothing they do here touches a live call.

## What it does

A form that builds one `ProtocolVersion` (`shared/contracts/models.py`). Four
things to fill in, and nothing else:

**1 · The interview.** Sections, and questions inside them. Per question: the
sentence the patient hears, the `fieldKey` it captures into, a label, the
capture type (text · enum · number · boolean · date), what to do if the answer
is unclear, and whether it blocks completion. This is the only part the patient
hears.

**2 · Red flags.** A list attached to the interview. Per flag: a label, the
literal phrases that trip it, and which of three types it is — critical
(`end_call`), urgent (`urgent_escalate`) or flagged (`soft_review`). A critical
flag also carries the exact sentence the assistant says. The gate is
deterministic and runs before generation, so these are patterns, not prompt
prose. The false-positive rule in `safety.py` applies: a flag may be critical or
urgent only if the correct response to a false positive is still acceptable.

**3 · Per-question flags.** The `QuestionFlag`s `concerns.py` resolves, authored
on a question: fired by **value** (a declared enum answer) or **judged** (the
model names it against the flag's `when`).

**4 · Tests.** A plain list, written as pairs: *what the patient says* → *what
should happen*. Nothing runs them. They are authored alongside the thing they
cover and stored with it, so the config carries its own evidence of intent.
Red flags already have one on the model (`provingUtterance`); this is the same
idea, extended to the script.

| Patient says | Expect |
|---|---|
| "sometimes I feel like I want to die" | call ends · `rf_self_harm` · crisis line spoken |
| "it started last Tuesday" | `onset_date` captured |
| "I don't know" | one follow-up, then recorded as unsure |

Save produces a new version a clinician can pick from the Deployments screen and
dispatch, and that `services/agent/` loads and runs.

## Explicitly out of scope

No forking, no publish gate. No test *runner* — the studio never executes
anything, calls no model, and has no pass/fail state. No tool matrix, no
report-guidance editor; those fields exist on the model and keep their current
defaults. The `agent-studio.html` spec covers all of them and is the direction
of travel, not this build.

## What already exists

- **The shape.** `ProtocolVersion` and everything under it are real pydantic
  models. `services/agent/config/protocol.py` holds the hardcoded instances,
  published as `PREOP_CHECK_V2` and `PREOP_SHORT_V2` beside the untouched v1s.
- **The storage.** `config.protocols` is a table, append-only by trigger and
  seeded from `protocol.py` at boot with `on conflict do nothing`. Publishing a
  version is an `insert`, never an edit, so a filed interview can never be moved
  by a later change.
- **Pinning.** Every interview pins the version it ran, and `reads.py` resolves
  flags through that pinned version.
- **What is offered.** `PROTOCOLS` is everything ever published and `OFFERED` is
  what may be dispatched; `GET /protocols` and `dispatch.py` read `OFFERED`.
- **Accounts and roles.** `config.accounts` is seeded, `shared/auth/` provides
  `require_role`, and the dashboard signs a clinician in.

## What it would need

- **Protocols read at runtime.** `PROTOCOLS` and `OFFERED` are still dict
  literals in a source file. A protocol authored in a browser has to be loaded
  from `config.protocols`, and "offered" has to become state on the row.
- **Routes to write one.** Everything in `routes/` reads or runs a call, or
  dispatches one; none authors. A write validates against the pydantic model and
  inserts.
- **An author role.** `require_role` has clinicians; who may author, and whether
  an authored protocol is visible to every clinician, is an authorisation
  decision and lives in `shared/auth/`.
- **A surface.** Either a fourth workspace (`app-studio` in `system-map.md`, a
  different audience from the dashboard) or a screen inside the dashboard.
- **The authored tests stored** with the version.

Nothing about the runtime changes: `machine.py` already walks a script,
`safety.py` already runs a `RedFlag` list, and `concerns.py` already resolves
`QuestionFlag`s. The studio's job is to be the second way a `ProtocolVersion`
comes into existence.
