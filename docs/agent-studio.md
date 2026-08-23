# Agent studio — `frontend/studio/`

The build-side half of the product. **Not built yet** — `frontend/studio/` does
not exist. The design spec `docs/ux/agent-studio.html` is much bigger than this;
what follows is the scope we actually want first.

## Who uses it

Whoever writes the interview — a clinician or clinical-safety lead, at a desk,
before any patient is involved. They are authoring config, not talking to
anyone. Nothing they do here touches a live call.

## What it does

A form that builds one `ProtocolVersion` (`shared/contracts/models.py`). Three
things to fill in, and nothing else:

**1 · The interview.** Sections, and questions inside them. Per question: the
sentence the patient hears, the `fieldKey` it captures into, a label, the
capture type (text · enum · number · boolean · date), what to do if the answer
is unclear, and whether it blocks completion. This is the only part the patient
hears.

**2 · Red flags.** A list attached to the interview. Per flag: a label, the
literal phrases that trip it, what it does (`end_call` or carry on and flag),
and — when it ends the call — the exact sentence the assistant says. The gate
is deterministic and runs before generation, so these are patterns, not prompt
prose.

**3 · Tests.** A plain list, written as pairs: *what the patient says* → *what
should happen*. Nothing runs them. They are authored alongside the thing they
cover and stored with it, so the config carries its own evidence of intent.
Red flags already have one on the model (`provingUtterance`); this is the same
idea, extended to the script.

| Patient says | Expect |
|---|---|
| "sometimes I feel like I want to die" | call ends · `rf_self_harm` · crisis line spoken |
| "it started last Tuesday" | `onset_date` captured |
| "I don't know" | one follow-up, then recorded as unsure |

Save produces a config the clinician dashboard can pick from a list and deploy,
and that `services/agent/` loads and runs.

## Explicitly out of scope

No versions, no forking, no publish gate. No test *runner* — the studio never
executes anything, calls no model, and has no pass/fail state. No tool matrix,
no escalation rota, no report-guidance editor; those fields exist on the model
and keep their current defaults. The `agent-studio.html` spec covers all of
them and is the direction of travel, not this build.

## What it would need

The data shape already exists — `ProtocolVersion` and everything under it are
real pydantic models, and `services/agent/config/protocol.py` is one hardcoded
instance of exactly what the studio would emit. Missing:

- **Somewhere to put them.** `PROTOCOLS` is a dict literal in a source file;
  the studio needs protocols that are created and read at runtime, which means
  the storage that `docs/system-map.md` still lists as unbuilt.
- **Routes to write one** — `services/core/app.py` has four, all about running
  a session, none about authoring.
- **Accounts** — same gap as the dashboard (`shared-auth`); patient portal has
  none by design, this cannot work without them.

Nothing about the runtime changes: `machine.py` already walks a script,
`safety.py` already runs a `RedFlag` list. The studio's job is to be the second
way a `ProtocolVersion` comes into existence.
