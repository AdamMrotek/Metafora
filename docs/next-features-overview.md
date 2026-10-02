# Next features — initial overview

Two features, both **draft and unscoped**: the Twilio call-out (5d) and the interview creator (the
studio). Neither blocks the other. This is the shape of each and the decisions that have to be made
before either gets a stage plan in the `phase-5-roadmap.md` format.

---

## 1 · Twilio call-out (5d)

**Goal.** A red flag that nobody has acknowledged rings a phone. First the owning clinician, then the
clinic's front desk. Today a red flag reaches an *open* dashboard (5b·2) and nothing else — the
roadmap's own words are "it will be seen", not "someone will call".

**How it works**

1. The gate's existing `on_escalation` callback (wired in `lifecycle.py`) already fires on every
   critical or urgent flag. A notifier subscribes alongside the broadcaster.
2. It dials the owning clinician with a TwiML `<Say>` + `<Gather>` — no LLM, no TTS vendor, no LiveKit.
   Pressing 1 hits a webhook that calls the existing acknowledge path.
3. A timer: if `acknowledged_at` is still null after N minutes, ring the front desk.
4. `notified_at` becomes a real event for the first time, because something now produces it.

**What changes**

| Area | Change |
|---|---|
| Schema | `phone` on `config.accounts` (seeded, as accounts always are); a clinic front-desk number in config; `notified_at` on `clinical.interviews` |
| Backend | a `comms/` module (sender + sweeper); two unauthenticated Twilio webhooks |
| Auth | Twilio signature verification lives in `shared/auth/` — invariant 5, even for a webhook |
| Config | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`; `REQUIRED_IN_PROD` and `scripts/preflight.py` updated |
| Docs | Twilio named as the **fourth egress** in `CLAUDE.md` |

**Constraints that shape it**

- **Invariant 3.** The spoken message carries nothing medical — not even the patient's name. "A
  Metafora alert needs acknowledging, please open the dashboard."
- **One machine.** The sweeper is in-process, like the broadcaster. Fine at one machine; the same limit
  `deployment.md` already assumes.
- **Webhooks need a public URL.** Fly already has one; local dev needs a tunnel.
- **Never fail the call.** A Twilio outage must not touch the patient's call — the notifier runs off
  the media path, same rule as `append`.

**Cost.** Runs to pennies per alert (roughly $0.05 per call), $1–2/month for a UK number, $20 minimum
top-up held as credit. A UK number needs a regulatory bundle, which can take days — **start that
first**. Roughly 2–3 days of engineering.

**Decisions to take**

1. Escalation window: how many minutes before the front desk is rung?
2. Retry policy when a call goes unanswered or to voicemail (voicemail "answering" a call is the usual trap).
3. SMS as well as a call, or a call alone?
4. Whose number for a demo — your own, with a note that the data is synthetic?

**Done when:** a red flag on a live call rings the owning clinician's phone; pressing 1 clears the
band; ignoring it rings the front desk.

---

## 2 · Interview creator (the studio)

**Goal.** A clinical-safety lead authors an interview in a browser, publishes it, and a clinician
can dispatch it — without a code change or a deploy. Today a protocol is a Python dict in
`services/agent/config/protocol.py`, and publishing `PREOP_CHECK_V2` meant a commit.

**What it produces.** One `ProtocolVersion` (`shared/contracts/models.py`) — the studio is the
*second way* a version comes into existence. `machine.py`, `safety.py` and `concerns.py` already walk
whatever they are handed, so the runtime does not change in shape.

**What is authored** (from `docs/agent-studio.md`, extended for what has shipped since)

1. **The interview** — sections, questions, the sentence the patient hears, `fieldKey`, capture type,
   what to do if unclear, whether it blocks completion.
2. **Red flags** — label, phrases, which of the three types (critical / urgent / flagged), and the
   `say` for a critical one. The false-positive rule from `safety.py` shows in the form as guidance.
3. **Per-question flags** — the `QuestionFlag`s `concerns.py` resolves: by value (a declared enum
   answer) or judged (the model names it against the flag's `when`).
4. **Tests** — *patient says → expect* pairs, authored and stored. Still no runner.

**Fits what is already there**

- `config.protocols` is **append-only by trigger** and versioned — "publish" is an `insert`, never an
  edit. That is a better match for the studio than the old doc imagined: no version-forking logic to
  invent, and a filed interview can never be moved by a later edit.
- `OFFERED` vs `PROTOCOLS` already separates "can run" from "can be dispatched". In the database this
  is a flag or a published-state on the row, not a second dict.
- `GET /protocols` and `dispatch.py` already read the list; they switch source, not shape.

**What changes**

| Area | Change |
|---|---|
| Backend | load protocols from `config.protocols` at runtime instead of the in-code dict; `routes/protocols.py` writes (validate with the pydantic model, insert); an `author` role through `require_role` |
| Schema | published/offered state; author and created-at provenance; the authored tests stored with the version |
| Frontend | a new surface — either a fourth workspace `frontend/studio/` (`:5175`) or a screen inside the dashboard. `docs/system-map.md` names it `app-studio` with a different audience, which argues for its own workspace |
| Seeds | `protocol.py` stays as the seed for v1/v2; new versions come only from the studio |

**Hard parts**

- **A running call must not see a version change under it.** Interviews already pin a version, so
  this should hold — confirm it explicitly with a test.
- **Authoring red flags is a clinical act.** The `safety.py` false-positive rule has to be visible at
  the point of authoring, and a flag set that ends calls deserves a review step before it is
  offered. Whether that is one person or two is a product decision, not a code one.
- **Role and scope.** `author` is a new kind of caller. Who may author, and whether an authored
  protocol is visible to every clinician or one practice, is an authorisation decision and belongs in
  `shared/auth/`.
- **Stale spec.** `docs/agent-studio.md` says accounts and storage do not exist. Both do now; it
  should be corrected when this is staged.

**Suggested first cut:** the form for sections, questions and red flags; publish as a new version;
dispatchable immediately. Authored tests stored but not run. No forking UI, no rota editor, no tool
matrix.

**Decisions to take**

1. Own workspace or a dashboard screen?
2. Who may author, and is there a publish gate?
3. Do per-question judged flags go in the first cut, or only phrase flags?
4. Can a published version be withdrawn from `OFFERED` — and by whom?

**Done when:** an author builds a protocol in the browser, publishes it, a clinician dispatches it
from the Deployments screen, and the call runs it — with no commit.

---

## Order

Independent. If one goes first, the call-out is smaller, has a hard external lead time (the UK number
bundle), and closes the gap the roadmap itself keeps naming. The studio is larger and changes who can
write clinical content, so it earns a proper stage plan before any code. Starting the Twilio number
registration now costs nothing and runs in the background while the studio is planned.
