# metafora.care — system map

**What this is.** One page that says which pieces of code exist and how they talk to each
other. High level on purpose: no technologies are chosen here, no schemas, no endpoints, no
vendors. Each piece gets its own folder (§2); this page is the index they hang off.

**Status:** draft · living document. Edit it whenever a piece is added, renamed, merged or
dropped. It should stay roughly this length. The Python migration
([`agent-review-and-pipecat-decision.md`](./agent-review-and-pipecat-decision.md))
merged `svc-core` and `svc-agent` into one process and deleted `svc-media`; this page reflects
that shape.

---

## 1 · The pieces

Three things a person opens, two things that run today, five places data rests, two ways we
reach a patient off-platform.

### Apps — what a browser loads

| Piece | Who opens it | What it is for |
|---|---|---|
| `app-dashboard` | Clinicians, admins | Read what the agent found, act on it, sign it off. The broad one — lists, patient records, one interview in detail. |
| `app-call` | Patients | One screen: join, talk, see what was captured. Entered from a link, not a login. |
| `app-studio` | Clinical safety leads, engineers | Author an interview, version it, run it against test cases before it goes live. Evals live here. |

### Shared code

| Piece | Used by | What it holds |
|---|---|---|
| `shared-ui` | all three apps | Design tokens and components, so the three products look like one product. Front-end only. |
| `shared-auth` | dashboard, studio | Sign-in, session, roles. `app-call` deliberately does **not** use it — a patient never holds an account credential. |
| `shared-contracts` | apps + services | The agreed shapes passed between front and back. One definition, both sides. |

### Services — what runs continuously

**Where they run:** `svc-core` and `svc-agent` are one Python process, and `svc-comms` is part
of that same process; `svc-media` was deleted by the migration — its job became a transport
inside the agent process, so it runs wherever the backend runs.

**How they connect:** everything a *person* does goes through `svc-core`, which is what keeps
permissions in one place. Two things bypass it: live audio, which terminates at the transport
inside the agent process, and a service recording what it produced itself — `svc-agent` writes
its own transcript. Neither of those is anyone's request for data, so neither needs a
permission check standing in front of it.

| Piece | Job | Talks to |
|---|---|---|
| `svc-core` | Reads and writes everything the dashboard and studio show: accounts, patients, interview definitions, results, transcripts, eval runs. The boring one, and the one that owns permissions. It also **pushes** the things that cannot wait to be asked for — an escalation raised mid-interview has to reach a clinician in seconds. | `store-clinical`, `store-config`, `store-transcript` (reads, never writes), `app-dashboard` |
| `svc-agent` | Runs a conversation. Fetches the interview definition, speaks with the patient, decides what to ask next, and **writes the transcript itself**, turn by turn, as the call happens. The transport the patient connects to lives here now. Two things it does not write but reports to `svc-core`: an escalation, the moment something cannot wait for the end of the call, and the interview's clinical outcome at the end. | `svc-core`, `store-transcript`, `store-metrics` |
| `svc-media` | **Deleted by the migration.** The connection service became a transport inside `svc-agent`, so nothing on this row exists as its own piece any more. The patient still connects to the same endpoint. | — |
| `svc-comms` | Everything that leaves our perimeter to reach a patient: invitation emails, reminders, scheduling. It **places** a call and hands it to the media path; it never carries one. Control plane, not data plane. | email + telephony providers |

### Stores — where data rests

Five, and the cut that matters most is between the two kinds of log. They are both "what
happened", and they are opposites: one is clinical evidence, the other is operational telemetry
that must be readable by anyone on call at 3am.

| Piece | Holds | Who may read it | Why it is separate |
|---|---|---|---|
| `store-clinical` | Patients, consent, interview results | Permission-gated, per patient | Most restricted thing we have. Smallest possible number of readers. |
| `store-transcript` | **Log type 1 — the conversation.** Turn-by-turn transcript, what the agent asked, what the patient said, what it decided and why | Permission-gated, same gate as the clinical record | This is medical data, no less sensitive than the clinical record. It is separate because it is bulky, append-only, and **written by a different service** — the one that had the conversation. |
| `store-metrics` | **Log type 2 — the system.** Latency, error rates, call volumes, queue depth, which step ran and how long it took | Anyone operating the system; no clinical permission needed | The one store that exists to be read freely, which is only possible if it contains **no medical data at all** — see rule 5. |
| `store-media` | Audio recordings and other large files | Permission-gated | Big, cheap, and the first thing a customer will want deleted on a schedule. |
| `store-config` | Accounts, roles, interview definitions and their versions, eval run records | Account holders | No patient data in it at all. |

The two log stores answer different questions. *"What did the agent ask this patient, and why?"*
is `store-transcript` and needs a permission check. *"Are calls dropping this morning?"* is
`store-metrics` and must not.

### Outside our perimeter

| Piece | For |
|---|---|
| Email provider | Sending an interview invitation or reminder. |
| Telephony provider | Calling a patient, or a patient calling in. Twilio is the obvious candidate, not yet a commitment. |

---

## 2 · Where the code lives

One repository, one folder per piece from §1. If a piece ever has to be pulled out into its own
repo or its own deploy, the move is a `git mv` and nothing else.

```
metafora.care/
|
|-- frontend/            what a browser loads
|   |-- dashboard/       clinician screens: lists, a patient record, one interview in detail
|   |-- call/            the patient's one screen: join, talk, see what was captured
|   |-- studio/          authoring interviews, versioning them, and the eval runs
|   `-- shared/          design tokens and components. Browser only; no service imports it
|
|-- shared/              base level, because both sides use it
|   |-- auth/            what a role is and what it grants, plus session handling
|   `-- contracts/       the shapes passed between pieces. One definition, both sides — owned by Python now, TypeScript generated |
|
|-- services/            one Python project, one process (see the migration decision)
|   |-- core/            session lifecycle HTTP, permissions, store access, dispatch queue
|   |-- agent/           the conversation loop: transport, ask, listen, decide, checkpoint
|   `-- comms/           anything that leaves the perimeter: email, placing a call (unstarted)
|
|-- db/                  one folder of migrations per store
|   |-- clinical/
|   |-- transcript/
|   |-- config/
|   `-- metrics/
|
`-- docs/                every document in the project
    |-- system-map.md    this page
    |-- pieces.md        what of each piece exists today, and how it is built
    `-- ux/              the frozen design specs, one per app
```

**All documentation lives in `docs/`, and nowhere else.** An app or service folder holds `src/`
and no prose, so there is exactly one place to look and exactly one place a fact can go stale.
The cut between the two main pages is what a page is *allowed* to say: this one is the shape and
the rules and names no technologies; [`pieces.md`](./pieces.md) is the state of the build and is
the only place a technology choice may appear.

**Schema and migrations.** A migration is owned by the **store**, not by whichever service
happens to be its only writer today, which is why `db/` is a peer of the others rather than
living inside `services/core/`. Types are generated one way from it into `shared/contracts`,
never hand-copied and never imported backwards.

**Folders are not deployments.** The tree says where code lives and what may call what; what
actually runs is fewer, larger processes on the split in §1. How they are released is a separate
question from this page.

---

## 3 · Rules that keep this shape

Five, and they are the reason the boxes fall where they do. They are proposals — argue with them
here rather than in code.

1. **Permission checks live in `svc-core`, not in the database.** The database enforces the coarse
   tenant boundary and nothing finer. A denial decided in `svc-core` can be logged and explained; a
   denial decided by a row policy is indistinguishable from a record that does not exist, which is
   the wrong thing to hand a clinician looking for a patient.
2. **A service writes the store it produced — `svc-agent` the transcript — and every other
   write goes through `svc-core`.** (The audio leg died with `svc-media`; nothing writes
   `store-media` today, though clinical-research requirements may bring audio retention back.)
3. **`app-call` holds no account credential.** A patient arrives with a link. It is a different
   auth posture from the rest of the product and it stays that way.
4. **Everything leaving the perimeter goes through `svc-comms`.** One place to audit, one place
   to swap a provider, one place a data-protection review has to look at.
5. **Nothing medical reaches `store-metrics`.** It carries ids, counts, durations and outcomes —
   a patient id is fine, what the patient said is not. Enforce it by what the metrics writer
   *accepts*: if the only way to record an event is a closed set of typed fields, a transcript
   line has nowhere to go.

---

## 4 · How it runs

### Two legs, so one can fail

Every patient connection terminates at the transport inside the agent process. The call is two
legs — patient to edge, edge to agent session — and the migration merged them onto one host and
accepted the cost of that (§6 of the decision doc): a crash in the backend takes every call on
it. What the split still buys is that the agent leg can break and re-form underneath a held
line, so a failing pipeline is a pause rather than a dial tone.

### Attach and resume

The mechanism itself belongs to `svc-agent`. What belongs on this page is only what it obliges
*other* pieces to do:

- **The backend mints and holds the session id.** Identity has to sit on the leg that survives, so
  the thing that answers *new call or continuation* is owned by the edge.
- **`store-transcript` enforces the write fence.** Rejecting a stale attach is a property of the
  store, not a convention between agent hosts.
- **The dashboard reads the recovery write.** There is no second progress record, which is what
  keeps the resume path exercised outside incidents.

### Where the model runs

There is no inference box today, and there is one the moment a model is self-hosted — GPU, a
different scaling axis, probably different hardware. What that move costs later is decided now by
one thing: **`services/agent` reaches a model through a single declared surface**, so moving
inference in, out, or onto the session host is a configuration change rather than a rewrite.
