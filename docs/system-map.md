# metafora.care — system map

**What this is.** One page that says which pieces of code exist and how they talk to each
other. High level on purpose: no schemas, no endpoints, no product decisions. Each piece gets
its own folder (§2); this page is the index they hang off.

**What this is not.** A compliant clinical system. This is a **portfolio deployment on synthetic
data** — patient audio terminates at a third party, and patient speech reaches a third-party
model. Both are deliberate, both are recorded in [§5](#5--what-this-deployment-is-not), and §5
is the only section that names a vendor. The point of the architecture is not that those gaps
are absent; it is that each one is **bounded, priced, and closed by a change this page names**.

**Status:** living document. Edit it whenever a piece is added, renamed, merged or dropped.
It should stay roughly this length. The Python migration
([`agent-review-and-pipecat-decision.md`](./agent-review-and-pipecat-decision.md)) merged
`svc-core` and `svc-agent` into one process and deleted `svc-media`; this page reflects that
shape.

Every claim below is tagged **Built**, **Partial** or **Unbuilt**. A row with no code behind it
says so, because a map whose promises are half-true is a map nobody can check.

---

## 1 · The pieces

Three things a person opens, three services, six places data rests, five parties outside the
perimeter.

### Apps — what a browser loads

| Piece | Who opens it | What it is for | State |
|---|---|---|---|
| `app-call` | Patients | One screen: join, talk, see what was captured. Entered from a link, not a login. | **Built** |
| `app-dashboard` | Clinicians, admins | Read what the agent found, act on it, sign it off. The broad one — lists, patient records, one interview in detail. | **Unbuilt** (roadmap §4) |
| `app-studio` | Clinical safety leads, engineers | Author an interview, version it, run it against test cases before it goes live. Evals live here. | **Unbuilt** (roadmap §6+) |

### Shared code

| Piece | Used by | What it holds | State |
|---|---|---|---|
| `shared-contracts` | apps + services | The agreed shapes passed between front and back. One definition, both sides — owned by Python, TypeScript generated. | **Built** |
| `shared-auth` | `svc-core` | What a role is and what it grants. The **backend** is its consumer, not the frontend: it verifies the token and resolves the account, and every authorisation decision in the product lives inside it rather than in a route body. It takes its issuer, its keys and its account directory as **parameters**, so it is generic over any OIDC provider — see §5. `app-call` deliberately does not use it: a patient never holds an account credential. | **Built** |
| `shared-ui` | dashboard, studio | Design tokens and components, so the products look like one product. Front-end only. | **Partial** — `tokens.css` exists; no components, and no second app to share them with yet. |

### Services — what runs continuously

**Where they run:** `svc-core` and `svc-agent` are one Python process. `svc-media` was deleted by
the migration — its job became a transport inside that process. `svc-comms` has no code yet.

**How they connect — and which way the arrow points.** Everything a *person* does goes through
`svc-core`, which is what keeps permissions in one place. Two things bypass it: live audio, which
terminates at the transport inside the process, and a service recording what it produced itself —
`svc-agent` writes its own transcript. Neither is anyone's request for data, so neither needs a
permission check standing in front of it.

Between the two services the dependency is **one-directional and points at the agent**:
`svc-core` imports `svc-agent`; `svc-agent` imports nothing from `svc-core` and is handed every
resource it needs, including its database pool. It is a library that `svc-core` drives. That is
rule 7, and it is what makes `services/agent/` the piece that could be lifted out.

| Piece | Job | Depends on | State |
|---|---|---|---|
| `svc-core` | Reads and writes everything the dashboard and studio show: accounts, patients, interview definitions, results, transcripts, eval runs. The boring one, and the one that owns permissions. It also **pushes** what cannot wait to be asked for — an escalation raised mid-interview has to reach a clinician in seconds — over a stream held open from this process, because a push that bypassed `svc-core` would be a clinical read with no permission check in front of it (rule 1). | `svc-agent`, `store-clinical`, `store-config`, `store-transcript` (reads, never writes) | **Built**; the push stream is **Unbuilt** (roadmap §5) |
| `svc-agent` | Runs a conversation. Fetches the interview definition, speaks with the patient, decides what to ask next, and **writes the transcript itself**, turn by turn, as the call happens. The transport the patient connects to lives here, and so do the two data-plane egresses (rule 5). Two things it does not write but reports upward: an escalation, the moment something cannot wait for the end of the call, and the interview's clinical outcome at the end. | nothing in this repo. Handed its pool, its keys and its protocol. | **Built** |
| `svc-comms` | Everything we initiate *toward* a patient: invitation emails, reminders, scheduling. It **places** a call and hands it to the media path; it never carries one. Control plane, not data plane. | email + telephony providers | **Unbuilt** — roadmap §5 creates it, and creates it as its own folder, because rule 4's whole value is that there is one directory to audit. |
| `svc-media` | **Deleted by the migration.** The connection service became a transport inside `svc-agent`. The patient still connects to the same endpoint. | — | Gone |

### Stores — where data rests

Six, and the cut that matters most is between the three kinds of log. They are all "what
happened" and they are not interchangeable: one is clinical evidence, one is operational
telemetry that must be readable by anyone on call at 3am, and one is the record of who read the
first two.

| Piece | Holds | Who may read it | Why it is separate | State |
|---|---|---|---|---|
| `store-clinical` | Patients, interview results, escalations, sign-off | Permission-gated, per patient | Most restricted thing we have. Smallest possible number of readers. | **Built** — patients, interviews, results. Escalations and signatures at roadmap §5. |
| `store-transcript` | **Log 1 — the conversation.** Turn-by-turn transcript, what the agent asked, what the patient said, what it decided and why | Permission-gated, same gate as the clinical record | This is medical data, no less sensitive than the clinical record. It is separate because it is bulky, append-only, and **written by a different service** — the one that had the conversation. | **Built** |
| `store-config` | Accounts, roles, interview definitions and their versions, eval run records | Account holders | No patient data in it at all. | **Built** — accounts and protocols. Eval runs unbuilt. |
| `store-metrics` | **Log 2 — the system.** Latency, error rates, call volumes, queue depth, which step ran and how long it took | Anyone operating the system; no clinical permission needed | The one store that exists to be read freely, which is only possible if it names **no patient at all** — see rule 6. | **Unbuilt** — the schema is claimed, nothing writes to it (roadmap §6+). |
| `store-audit` | **Log 3 — the readers.** Who opened which record, when, and under what grant | Compliance, not on-call | It cannot live in `store-metrics`, because *"clinician X read patient Y's transcript"* names a patient and rule 6 forbids that; it cannot live in `store-clinical`, because the people who need to read it are not the people permitted to read patients. Cheap to name now, painful to backfill after six months of unlogged reads. | **Unbuilt** — no schema. Wanted by break-glass access (roadmap §6+). |
| `store-media` | Audio recordings and other large files | Permission-gated | Big, cheap, and the first thing a customer will want deleted on a schedule. | **Unbuilt** — nothing records audio. Clinical-research requirements may bring it back (roadmap §6+). |

The three logs answer different questions. *"What did the agent ask this patient, and why?"* is
`store-transcript` and needs a permission check. *"Are calls dropping this morning?"* is
`store-metrics` and must not. *"Who looked at this record last March?"* is `store-audit` and
needs a different permission check from either.

### Outside our perimeter

Five parties, in three classes, and the classes have different properties. §5 prices the swap for
each and names the vendors; these tables name only the roles, because the role is the shape and
the vendor is a deployment decision.

**Data plane — carries patient content.** This is the class the perimeter rule is really about,
and the class the previous version of this page omitted.

| Role | Carries | Opened by |
|---|---|---|
| Media transport | Patient audio, live | `svc-agent` |
| Model provider | Every patient utterance (speech-to-text), every utterance again (the model), and the agent's replies (text-to-speech) | `svc-agent` |

**Control plane — carries our messages to a patient, never their content.**

| Role | For | Opened by |
|---|---|---|
| Email provider | Sending an interview invitation or reminder. | `svc-comms` |
| Telephony provider | Calling a patient, or a patient calling in. | `svc-comms` |

**Operational egress — carries our failures, and carries patient content only if we let it.**

| Role | Carries | Opened by |
|---|---|---|
| Error reporting | Unhandled exceptions from `svc-core`: type, message, stack, request metadata | `svc-core` |

This is the class with the interesting property, and the reason it is not simply filed under the
data plane. An error reporter is *structurally* able to carry everything the other two classes do —
a stack frame holds locals, and a request body holds what the patient typed — so it is the one
egress whose safety is a **construction rather than a category**. Two things make it true and both
are code: the client is given no request bodies at all, and every event raised inside
`services/agent/` is dropped before it is sent. `services/agent/` is where the conversation is, so
the boundary rule 7 draws for architectural reasons turns out to be the same line privacy needs —
see rule 7.

**Managed dependencies** are a third thing and not an egress: the record store (plain Postgres)
and the identity provider (OIDC + JWKS). Nothing of ours leaves through them that is not already
at rest in a store above, and both are swappable — §5.

---

## 2 · Where the code lives

One repository, one folder per piece from §1. If a piece ever has to be pulled out into its own
repo or its own deploy, the move is a `git mv` and nothing else — which is true in the direction
rule 7 protects, and only that direction.

```
metafora.care/
|
|-- frontend/            what a browser loads
|   |-- call/            the patient's one screen: join, talk, see what was captured
|   |-- dashboard/       clinician screens: lists, a patient record, one interview in detail
|   |-- studio/          authoring interviews, versioning them, and the eval runs (unbuilt)
|   `-- shared/          design tokens and components. Browser only; no service imports it
|
|-- shared/              base level, because both sides use it
|   |-- auth/            what a role is and what it grants. Reads no environment of its own
|   `-- contracts/       the shapes passed between pieces — owned by Python, TypeScript generated
|
|-- services/            one Python project, one process (see the migration decision)
|   |-- core/            session lifecycle HTTP, permissions, store access, dispatch queue
|   |-- agent/           the conversation loop: transport, ask, listen, decide, checkpoint
|   `-- comms/           anything we initiate toward a patient: email, placing a call (unbuilt)
|
|-- supabase/            the schema: one migration directory, one schema per store
|   `-- migrations/
|
`-- docs/                every document in the project
    |-- system-map.md    this page — the shape and the rules
    |-- roadmap.md       what is left to build, in order, and what is done
    |-- deployment.md    where it runs, what it costs, what blocks it
    |-- roadmap-review.md   the audit that caught what a phase promised and did not build
    |-- agent-review-and-pipecat-decision.md   why Pipecat, why Python
    |-- patient-portal.md · clinical-dashboard.md · agent-studio.md   one per app
    |-- example-interview.md
    `-- ux/              the frozen design specs, one per app
```

**All documentation lives in `docs/`, and nowhere else.** An app or service folder holds `src/`
and no prose, so there is exactly one place to look and exactly one place a fact can go stale.
Three pages carry the load and the cut between them is what each is *allowed* to say: **this
page** is the shape and the rules; [`roadmap.md`](./roadmap.md) is what is built and what is
next; [`deployment.md`](./deployment.md) is where it runs and what that costs. A technology
choice may appear on any of the three — silence about vendors is what let this page claim a
single audited perimeter while patient audio left through an unnamed one.

**Schema and migrations.** A migration is owned by the **store**, not by whichever service
happens to be its only writer today, which is why the migration directory is a peer of the
others rather than living inside `services/core/`. The stores share one database and take a
schema each, because the boundary that matters is which code may touch which table, not which
host it sits on. Types are generated one way from `shared/contracts`, never hand-copied and
never imported backwards.

**Folders are not deployments.** The tree says where code lives and what may call what; what
actually runs is fewer, larger processes on the split in §1.

---

## 3 · Rules that keep this shape

Seven, and they are the reason the boxes fall where they do. Each is tagged **Held** — true today
and something enforces it — or **Preserved** — not true today, and the code is arranged so that a
change §5 names makes it true. A Preserved rule is a seam, not a promise.

1. **Permission checks live in `svc-core`, not in the database.** **Held.** The database enforces
   the coarse tenant boundary and nothing finer. A denial decided in `svc-core` can be logged and
   explained; a denial decided by a row policy is indistinguishable from a record that does not
   exist, which is the wrong thing to hand a clinician looking for a patient.

   Phase 2 built this and added the habit that keeps the door open to something finer: the check
   yields an identity, and the *query* takes it as a parameter. Scope is a `where` clause in
   `svc-core`'s SQL, not a role read at the door and then forgotten.

   **This rule constrains the escalation push.** A dashboard subscribing directly to the database
   for live escalations is a person reading clinical data on a path that never reaches
   `svc-core`, and the only enforcement available there is row-level security — the mechanism this
   rule rejects, deferred at roadmap §6+ precisely so the trade is made deliberately. So the push
   is a stream held open **from `svc-core`**, which is already a long-lived, session-affine
   process. The day rules over relationships are wanted, row-level security wants per-request
   identity on the connection and `svc-core` holds one pooled service connection; that trade is
   to be made deliberately, not discovered.

2. **A service writes the store it produced — `svc-agent` the transcript — and every other write
   goes through `svc-core`.** **Held.**

3. **`app-call` holds no account credential.** **Held.** A patient arrives with a link. It is a
   different auth posture from the rest of the product and it stays that way. It is also why the
   routers are split by audience: two opposite postures in one file is a file where the next route
   lands on the wrong side by accident.

4. **Everything we initiate toward a patient goes through `svc-comms`.** **Preserved** —
   `svc-comms` has no code. One place to audit, one place to swap a provider, one place a
   data-protection review has to look at. Note the scope: this rule is about the **control
   plane**. It has never covered patient audio, and the previous version of this page implied it
   did.

5. **The data-plane egresses are a closed, enumerated set, and only `svc-agent` opens one.**
   **Held by inspection, Preserved as enforced.** Today the set is exactly two — media transport
   and model provider — and both are constructed inside `services/agent/`. Nothing yet *stops* a
   third being added, which is what "Preserved" means here: the enforcement is the same shape as
   rule 6's, a narrow declared surface rather than a convention, and §5 names it.

   The near-miss is worth naming. Error reporting is opened by `svc-core`, not `svc-agent`, and
   is a third class rather than a third member of this set — but only because rule 6 keeps
   patient content out of it. Had it been added carelessly it would have been a data-plane egress
   that nobody classified as one, which is the shape of most accidental disclosures.

6. **Nothing medical reaches telemetry, and telemetry names no patient at all.** **Held for the
   sink that exists, Preserved for the one that does not.**

   *Held* — error reporting is live and the rule is enforced by construction, not by review: the
   client receives no request bodies (`POST /session/{id}/typed` carries what the patient typed),
   and events raised inside `services/agent/` are dropped before send, because a stack frame from
   the conversation holds transcript. Both are tested. This is the rule's proof that its
   enforcement style works.

   *Preserved* — `store-metrics` has no writer, so nothing has broken it and nothing enforces it.
   Ids of *runs*, counts, durations and outcomes are fine; a patient id is not, because the thing
   that wants to record a patient id in the freely-readable store is an access log, and that is
   `store-audit`. Enforce it the same way: if the only way to record an event is a closed set of
   typed fields, a transcript line has nowhere to go.

7. **`services/agent/` imports nothing from `services/core/`.** **Held.** Every resource it needs
   is handed to it — its database pool arrives as a constructor argument, its keys and its
   protocol as parameters. `shared/auth/` follows the same discipline for the same reason. This is
   currently true and one convenient import destroys it silently, which is why it is a rule rather
   than an observation: it is what makes the conversation loop a library, what lets the test suite
   stand pieces up in isolation, and what would make an extraction a `git mv`.

   **This boundary now carries a second job.** Error reporting drops any event whose frames come
   from `services/agent/`, which means the import line is also the privacy line: the module path
   is a reliable proxy for *did a transcript pass through here* precisely because the dependency
   is one-directional. If `services/core/` code ever moves into `services/agent/`, or the arrow
   reverses, the scrub silently starts covering the wrong set. A rule with two unrelated
   consumers is a rule worth a test.

---

## 4 · How it runs

### Two legs, so one can fail

Every patient connection terminates at the transport inside the agent process. The call is two
legs — patient to edge, edge to agent session — and the migration merged them onto one host and
accepted the cost of that (§6 of the decision doc): a crash in the backend takes every call on
it. What the split still buys is that the agent leg can break and re-form underneath a held
line, so a failing pipeline is a pause rather than a dial tone.

### One call, one process — and how a second one becomes possible

**Built.** A session's live handles are process-local, so every request about a call has to reach
the process running it. That makes the backend session-affine, which is a constraint the hosting
inherits rather than one it can choose (`deployment.md` §1).

The shape that keeps a second machine from being a wall of intermittent 404s: the process that
serves the call **names itself in the response**, and the browser returns that name on every
subsequent request about that call. It is dead weight on a single machine and the difference
between an afternoon of debugging and a config change on the day there are two — which is the
only kind of thing this page should be recording ahead of time.

### Shutdown is the goodbye, not a grace period

**Built.** There is no wait for calls to end naturally. On a signal the process hangs every live
call up and gives each a few seconds to finish the sentence it is speaking; the platform's kill
timeout only has to outlast that. Releases are blue/green, never rolling, because two versions
serving one session-affine surface is the case the affinity header cannot rescue.

This is worth stating plainly because the obvious alternative — *drain by waiting out the longest
interview* — sounds like a deployment setting and is actually a change to the call lifecycle. It
is not configured anywhere, and a page that implied it was would send someone to the wrong file.

### Attach and resume — **design held in reserve**

**Unbuilt.** Reconnect and resume are roadmap §6+. This section records the obligations the
mechanism would place on *other* pieces, so that the choices made before then do not foreclose it:

- **The backend mints and holds the session id.** *Already true.* Identity has to sit on the leg
  that survives, so the thing that answers *new call or continuation* is owned by the edge.
- **`store-transcript` enforces the write fence.** *Not true.* The append-only trigger stops
  deletion; it does not reject a stale writer. This is the one piece that is **cheap now and
  expensive later** — a writer-generation column on `transcript.events` is a migration today and
  an unbackfillable one after the table has history. Decide it deliberately; building the resume
  path itself can wait.
- **The dashboard reads the recovery write.** *Not applicable yet.* There is no second progress
  record, which is what would keep the resume path exercised outside incidents.

### Where the model runs

There is no inference box today, and there is one the moment a model is self-hosted — GPU, a
different scaling axis, probably different hardware. What that move costs is set by whether
`services/agent/` reaches a model through one declared surface. **It does not today**: the
pipeline names its vendor's classes directly and one of them is inherited from. §5 prices it, and
it is the single largest gap on this page.

---

## 5 · What this deployment is not

The compliance delta, in one table. **Property** is what a clinical deployment would need.
**Closes it** is the change. **Proof** is the thing in this repo that demonstrates the seam is
real rather than asserted — because a preserved seam nobody exercises is a claim.

| Property | True today | Closes it | Cost | Proof |
|---|---|---|---|---|
| **Media never leaves our infrastructure** | No — LiveKit Cloud terminates patient audio | Point `LIVEKIT_URL` at a self-hosted SFU | **Zero code.** Ops only: TLS on `wss`, UDP 50000–60000, and TURN over TCP/443 — which matters because clinic and hospital firewalls block UDP, and that is exactly where the patients sit | **`make dev`** runs `livekit-server` locally. The self-hosted configuration is the one every developer uses every day |
| **Patient speech never reaches a third-party model** | No — speech-to-text and the model itself are Groq | A model surface in `services/agent/`, then a base URL | One module and one import change in `pipeline.py`. Groq's LLM and STT are OpenAI-compatible, so a self-hosted vLLM or Whisper is the same code path | **None.** This is the open gap: `pipeline.py` constructs `GroqLLMService` and `GroqSTTService` inline |
| **The agent's voice is ours** | No — Groq/Orpheus | Re-pick a text-to-speech vendor | A rewrite of `services/agent/tts.py`, which **subclasses** the vendor's service to strip Orpheus's silence padding. This leg does not move by configuration and the page should not pretend it does. It is also the least sensitive of the three: it carries the agent's words, not the patient's | n/a |
| **The clinical record sits on infrastructure we control** | No — managed Postgres | `DATABASE_URL` | A connection string. The schema is plain Postgres — no proprietary extensions, no vendor functions | **`make test-pg`** runs every migration against a throwaway Postgres that is not the managed one |
| **Identity is not tied to one vendor** | **Yes** | — | — | **`tests/test_auth.py`** stands the whole authenticated backend up on an EC keypair generated in a fixture and a JWKS served from memory, with no project and no network. `shared/auth/` takes the issuer, the keys and the directory as parameters |
| **Failure reports carry no patient content** | **Yes** | — | — | The client is given no request bodies, and every event raised inside `services/agent/` is dropped before send. Both scrubs are asserted in `tests/test_app.py` — including the two cases that must *not* be dropped, so the test fails if the filter starts swallowing everything |
| **Live escalations do not bypass the permission check** | Not yet built | A stream held open from `svc-core` (rule 1) | The alternative — subscribing the browser to the database — is cheaper to write and forfeits both rule 1 and the swappable-Postgres row above, since it is the vendor's proprietary service and not Postgres | n/a |
| **Access to a clinical record is logged** | No | `store-audit` | Unbuilt; a schema and a write on every read path in `reads.py`. Cheap while there are three read routes | n/a |
| **Nothing medical in operational telemetry** | Vacuously — nothing writes telemetry | A metrics writer that accepts a closed set of typed fields | Unbuilt (roadmap §6+). The enforcement is the writer's signature, not a review | n/a |
| **Audio is retained and deleted on a schedule** | No — nothing records audio | `store-media` plus a deletion schedule | Unbuilt (roadmap §6+) | n/a |
| **A dropped call resumes** | No | Roadmap §6+ | The only part that is cheap now is the write fence — see §4 | n/a |

**What consent is doing here: nothing.** No consent is captured, stored, or modelled anywhere in
this system. An earlier version of this page listed it in `store-clinical`; it was never true, and
on a demo where a stranger talks to a clinical agent, claiming a consent record you do not hold is
worse than holding none.

**The honest summary.** Five of the properties above are already true or close by configuration,
and four of those have a proof sitting in the repo. One — patient speech reaching a third-party
model — is a refactor, not a rewrite, and is unbuilt. The rest are unbuilt features that this
page names so they are not discovered late. That distinction is the architecture; the demo is
just where it currently points.
