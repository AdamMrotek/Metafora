"""The artefact that travels between all three layers of Metafora.

`docs/ux/agent-studio.html` defines it: six blocks, frozen at publish, never
edited in place. The studio authors it, the evals runner proves it, and the
clinician portal deploys it to a patient. This file is the contract.

Note the studio's own warning: four of the six blocks never enter the prompt
at all. The break is a gate that runs before generation, the routes are
policy, the tool table is a permission matrix enforced in-process. Writing
them as prose in a system message is exactly how they stop being enforceable.

Since the migration this file is also the *source* of truth: the TypeScript in
`shared/contracts/src/` is generated from here, not hand-mirrored. Nothing in
the backend consumes TypeScript any more, so the direction of generation
reversed.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Python reads snake_case; the wire and the generated TypeScript stay camelCase.

    `frontend/call` was written against hand-authored TypeScript and must not
    notice that the source of truth moved to Python, so serialisation aliases
    rather than a rename is the whole compatibility story.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

# ─── The severity vocabulary ─────────────────────────────────────────────────
#
# One list, shared by both blocks that can raise something. A question flag
# (Block I) and a red flag (Block II) reach the same escalation band, the same
# `worst_flag` on the review row and the same ranking in `safety.SEVERITY`, so a
# second vocabulary for *how bad is this* would be a second answer to a question
# the dashboard has to answer once. The name is Block II's because that is where
# the record already files it; it is not only Block II's any more.

RedFlagAction = Literal["end_call", "urgent_escalate", "soft_review", "note_only"]


# ─── Block I · interview script ──────────────────────────────────────────────


class TextCapture(CamelModel):
    type: Literal["text"] = "text"


class EnumCapture(CamelModel):
    type: Literal["enum"] = "enum"
    values: list[str]


class NumberCapture(CamelModel):
    type: Literal["number"] = "number"
    unit: str | None = None


class BooleanCapture(CamelModel):
    type: Literal["boolean"] = "boolean"


class DateCapture(CamelModel):
    type: Literal["date"] = "date"


#: How an answer is captured into the record. Only `text` is implemented.
Capture = Annotated[
    TextCapture | EnumCapture | NumberCapture | BooleanCapture | DateCapture,
    Field(discriminator="type"),
]


class QuestionFlag(CamelModel):
    """A concern that belongs to one question's answer rather than to the call.

    A `RedFlag` scans every turn for a phrase and knows nothing about what was
    asked, which is why *"No."* to *are you still able to come?* raises nothing:
    it is a cancellation with no phrase in it to match. This is the other half.
    It is evaluated once, against the answer to the question it hangs off, so
    the question supplies the meaning the words do not carry on their own.

    Two triggers, and a flag may declare either or both:

      · `when_value` is one of the values this question's `EnumCapture` already
        declares. A table lookup — no model, no phrasing, no negation problem.
        Every condition the enum can express should be written this way.
      · `when` is a sentence a model judges the answer against, for the ones it
        cannot: metaphor, indirection, a hedge that is really a refusal.

    Whichever fires, it is the same flag filing the same `id`, so the escalation
    band never has to tell a lookup from a judgement.

    Note what this is **not**. Block II runs before generation, on every turn,
    and cannot be talked out of a match. This runs on an answer, after it, and
    where `when` is doing the work a model's opinion is in the loop. It is a
    second net under the gate and never a replacement for it.
    """

    #: Opaque, and filed. A hit is recorded by id and resolved back against the
    #: version the interview pinned, exactly as a red flag's is — so an id must
    #: be unique across *both* lists in a version, and renaming one orphans
    #: everything already filed under it.
    id: str
    #: What the clinician reads on the escalation band.
    label: str
    #: A value of this question's `EnumCapture`, matched exactly. Deterministic.
    when_value: str | None = None
    #: The same condition in words, for what an enum cannot express.
    when: str | None = None
    action: RedFlagAction
    #: Spoken to the patient when this stops the call, and only then. Any other
    #: action lets the conversation continue, which means the model has already
    #: written the reply it says next — a second sentence would race it into the
    #: same TTS. `test_flag_types.py` holds that line for red flags and for
    #: these.
    say: str | None = None
    #: The answer that must raise it — the question-level counterpart of
    #: `RedFlag.proving_utterance`, and the same discipline: a flag nobody can
    #: show firing is a flag nobody can test.
    proving_answer: str

    @model_validator(mode="after")
    def _declares_a_trigger(self) -> "QuestionFlag":
        if self.when_value is None and self.when is None:
            raise ValueError(
                f"question flag {self.id!r} declares no trigger: "
                "set when_value, when, or both"
            )
        return self


class Question(CamelModel):
    id: str
    #: Spoken to the patient. The only block a patient hears in full.
    ask: str
    #: Where the answer lands in the record, and on the clinician's composer.
    field_key: str
    #: Human label for the "Notes so far" card and the review composer row.
    label: str
    capture: Capture
    #: The question reads as yes/no but asks for *content*. "Before we finish,
    #: is there anything else?" is grammatically answerable with "yes", and a
    #: model reading it literally will record exactly that — which is the
    #: patient saying they have something to tell you, not telling you it. On
    #: `iv_53ff71f5e583` that recorded `anything_else = "Yes"`, completed the
    #: interview and hung up on a patient who had just said she had something
    #: to raise. Declared here rather than inferred, because whether a question
    #: wants a fact or a confirmation is the author's to say.
    expects_content: bool = False
    #: What to do when the answer does not resolve. Drives the follow-up budget.
    if_unclear: str | None = None
    #: A section may be skipped by policy; a question may not.
    must_capture: bool
    #: What this *answer* may raise. Empty means the Block II gate is the only
    #: thing watching this question, which is what every v1 question means.
    flags: list[QuestionFlag] = []


class Section(CamelModel):
    id: str
    title: str
    questions: list[Question]


class InterviewScript(CamelModel):
    sections: list[Section]


# ─── Block II · safety break ─────────────────────────────────────────────────
#
# `RedFlagAction` is declared above Block I, because a question flag is
# authored at the same three levels this is.


class RedFlag(CamelModel):
    """Runs on the committed turn, before any generation — so the model is not in
    the loop by construction. Every entry carries the utterance that must trip
    it, which is what makes the fixture generatable.
    """

    id: str
    label: str
    #: Deterministic match. Compiled case-insensitively against the transcript.
    patterns: list[str]
    #: Only one outcome stops the conversation.
    action: RedFlagAction
    #: The sentence spoken to the patient when this trips, if the call ends.
    say: str | None = None
    #: The utterance that proves it fires. Generates the suite fixture.
    proving_utterance: str


# ─── Blocks III & IV · escalation ────────────────────────────────────────────


class UrgentEscalation(CamelModel):
    #: A rota with a timeout, never a person.
    rota: list[str]
    timeout_minutes: int
    #: What the patient is told when an urgent flag fired: once, after the
    #: goodbye, never at the time. Authored here rather than on the flag so that
    #: two urgent flags on one call still produce one sentence — it is a
    #: statement about the call, not about a match.
    #:
    #: Optional, and for two reasons. A protocol that authors no urgent flag has
    #: nothing to say; and the versions already in `config.protocols` were
    #: published without it and have to keep loading, because that table is
    #: append-only and a stored version is what a filed interview reads through.
    closing: str | None = None


class SoftReviewTrigger(CamelModel):
    id: str
    reason: str


# ─── Block V · tools ─────────────────────────────────────────────────────────


class ToolSpec(CamelModel):
    """A matrix, not a list: what it may call, from which states, how many times."""

    name: str
    description: str
    #: Fully-qualified state ids (`sectionId.questionId`) this tool is legal in.
    allowed_states: list[str]
    max_attempts_per_turn: int


# ─── Block VI · report guidance ──────────────────────────────────────────────


class ReportGuidance(CamelModel):
    #: Field keys the review composer renders, in this order.
    fields: list[str]
    rules: list[str]


# ─── The published version ───────────────────────────────────────────────────


class Clinician(CamelModel):
    name: str
    initials: str
    practice: str
    #: The line under the name: why this call is happening.
    context: str


class ProtocolVersion(CamelModel):
    """One immutable ProtocolVersion. Every interview pins one, so a report that
    says "severity 7" can always be read back against the question that was asked.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True
    )

    id: str
    label: str
    frozen: Literal[True] = True
    clinician: Clinician
    script: InterviewScript
    red_flags: list[RedFlag]
    urgent: UrgentEscalation | None
    soft_review: list[SoftReviewTrigger]
    tools: list[ToolSpec]
    report: ReportGuidance


# ─── Runtime records ─────────────────────────────────────────────────────────


class Patient(CamelModel):
    id: str
    first_name: str


class QueuedInterview(CamelModel):
    """A dispatch-queue entry. In production this comes off a queue; for now, one."""

    id: str
    protocol_id: str
    patient: Patient


FieldStatus = Literal["pending", "live", "open", "captured"]


class FieldState(CamelModel):
    """One line of the "Notes so far" card, and one field on the review composer."""

    key: str
    label: str
    value: str | None
    status: FieldStatus


CallPhase = Literal["idle", "listening", "thinking", "speaking", "ended"]


# ─── The read surface ────────────────────────────────────────────────────────
#
# What `GET /interviews`, `GET /interviews/{id}` and `GET /patients` return.
# Written here rather than in the router because the dashboard is the other end
# of them and reads the generated TypeScript, so these shapes are a contract in
# exactly the sense the rest of this file is.

InterviewStatus = Literal["queued", "running", "completed", "abandoned"]

#: Whether a patient is someone a clinician dispatched a call to, or an artefact
#: of the public demo link being clicked. A caseload that cannot tell them apart
#: is a caseload full of strangers.
PatientOrigin = Literal["demo", "dispatched"]


class InterviewSummary(CamelModel):
    """One row of the review table."""

    id: str
    status: InterviewStatus
    #: How the call ended, in the store's vocabulary — `complete`, `safety`,
    #: `patient_left`. Null until it has ended.
    outcome: str | None
    patient_id: str
    patient_first_name: str
    #: The patient's identity, as `clinical.patients` holds it. Null for a row
    #: this product created itself — the intake asks for a first name, and a
    #: real deployment carries the rest across at dispatch. On the summary
    #: rather than behind a detail fetch because the review table draws a
    #: masked NHS number on every line.
    patient_nhs_number: str | None
    patient_date_of_birth: date | None
    patient_origin: PatientOrigin
    protocol_id: str
    protocol_label: str
    #: How much of the script the call actually got through. `captured_fields`
    #: is counted from `clinical.results`; `total_fields` from the questions the
    #: pinned protocol declares, because a call that has not ended yet has no
    #: result rows and 0/0 would read as an empty script. On the row rather than
    #: behind a second request because the review table draws a progress meter
    #: per line, and one lateral join is cheaper than a detail fetch per row.
    captured_fields: int
    total_fields: int
    #: How many distinct red flags the gate matched across the whole call, and
    #: the most serious action any of them carried. Null and zero mean the same
    #: thing said twice: the gate ran on every turn and nothing tripped it.
    #:
    #: `outcome` only distinguishes the flag that *stopped* the call
    #: (`end_call` → `safety`). An `urgent_escalate` or a `soft_review` lets the
    #: conversation finish, so without these two fields a flagged call and a
    #: clean one are the same row, and the clinician's own summary cannot tell
    #: them apart. That is the number the dashboard counts.
    flag_count: int
    worst_flag: RedFlagAction | None
    scheduled_for: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class ProtocolOption(CamelModel):
    """One entry of the composer's *what* field.

    The id and the label only. A composer that had to fetch whole
    `ProtocolVersion`s to fill a select would be pulling every question of every
    protocol across to draw four words.
    """

    id: str
    label: str


#: The two orders the review table offers. `urgency` is a clinical ordering and
#: not a display preference — an escalation from yesterday outranks a completed
#: call from ten minutes ago, because the table exists to be worked through.
InterviewSort = Literal["urgency", "recent"]


class InterviewPage(CamelModel):
    """One page of the review table, and enough to draw the pager under it.

    The rows used to be the whole list: `GET /interviews` returned the hundred
    most recent and the browser sorted, searched and paged them. That is fine at
    twenty interviews and wrong at a hundred and one, because the browser would
    be triaging a window it cannot see past — so `total` is here, counted in the
    same statement as the rows, and it is the number that says whether anything
    was left out.
    """

    rows: list[InterviewSummary]
    #: Rows matching the filter across the caller's whole scope, not on this page.
    total: int
    page: int
    #: Always at least 1, so an empty table still reads "page 1 of 1" rather
    #: than "page 1 of 0".
    pages: int


class Escalation(CamelModel):
    """One line of the escalation band: a red flag nobody has taken yet.

    Not an `InterviewSummary`. The band is a worklist line rather than a table
    row, and the two things it has to say — *which* flag, and *by when* — are
    neither of them on the summary: `worst_flag` is the action string, and
    there is no scan timestamp on the row at all. Widening `_SUMMARY_COLUMNS`
    to carry them would put two columns on every row of the review table that
    only a banner ever reads.

    It also replaces what the browser used to do: fetch the whole transcript of
    the flagged interview and reconstruct the patient's words from the turn the
    gate scanned. The label says what the quote was trying to say, and says it
    from the protocol the interview pinned.
    """

    interview_id: str
    patient_first_name: str
    #: The flag's own label, resolved against the `ProtocolVersion` this
    #: interview pinned — so a re-authored flag set never renames an old line.
    flag_label: str
    #: `end_call` (the call was stopped — make contact) or `urgent_escalate`
    #: (a decision is owed by `due_at`). The band tells them apart from this
    #: rather than from a second field.
    action: RedFlagAction
    #: When the gate scanned the turn, not when the call ended. On a triage
    #: flag the call runs on for minutes afterwards, and the clock started at
    #: the scan.
    raised_at: datetime
    #: `raised_at` plus the pinned version's `urgent.timeout_minutes`, so
    #: re-authoring a timeout never retro-moves a deadline that was already
    #: running. Null when the protocol declares no urgent escalation.
    due_at: datetime | None


class Acknowledgement(CamelModel):
    """What `POST /interviews/{id}/acknowledge` answers with.

    It means *I have this* and nothing more — no disposition, no close reason.
    What was decided belongs to the sign-off or to the practice's own systems.
    Idempotent: a second POST returns the first stamp and the first
    acknowledger rather than reassigning who owns the decision.
    """

    interview_id: str
    acknowledged_at: datetime
    acknowledged_by: str


class Overview(CamelModel):
    """Everything on the dashboard that is not one page of the table.

    It exists because the table stopped being the whole list. Every one of these
    was computed in the browser from the same fetch the table used, which was
    workable only while that fetch was the whole record — the moment it became
    one page of eight, a tile counting it would have been counting a page and
    calling it the caseload.
    """

    #: The three tiles. Three errands, not a census: a red nobody has taken
    #: yet, what the gate flagged on a call that ran on and that somebody has,
    #: and what neither happened to but which stopped short of its script.
    urgent: int
    flagged: int
    incomplete: int
    #: Unacknowledged red flags, newest first, for the band above every screen.
    #: Capped — `urgent` is the count it states, and both are counted over the
    #: caller's whole scope so the band's sentence and the tile above it cannot
    #: disagree about the same number.
    escalations: list[Escalation]
    #: Calls still out, soonest first: the scheduled card and the Deployments
    #: screen's upcoming table are the same rows asked twice.
    queued: list[InterviewSummary]
    #: What the protocol filter may offer — the protocols present in this
    #: caller's record, never every protocol that exists. A filter listing an
    #: option that selects nothing is a filter that lies about the record.
    protocols: list[ProtocolOption]


class ResultField(CamelModel):
    """One captured field, as the review composer renders it."""

    field_key: str
    label: str
    value: str | None
    status: FieldStatus
    updated_at: datetime


class TranscriptEvent(CamelModel):
    """One line of `transcript.events`, as the writer appended it.

    `payload` is the whole event, unflattened, because the union of event shapes
    lives in `services/agent/session_log.py` and re-declaring fifteen variants
    here to serve one screen would be two sources of truth for the same record.
    """

    seq: int
    type: str
    at: datetime
    payload: dict[str, Any]


class InterviewDetail(CamelModel):
    """One interview, everything about it, in one request.

    The transcript carries **every** safety scan, including the ones that
    matched nothing. A route that filtered them would look identical and would
    quietly remove the only evidence that the gate ran on a turn it cleared.
    """

    interview: InterviewSummary
    results: list[ResultField]
    events: list[TranscriptEvent]
    #: Every interview for this patient, oldest first, including this one — the
    #: detail screen's timeline. Fetched with the thing it is a timeline of,
    #: rather than filtered out of the dashboard's list, so opening an interview
    #: directly from a bookmark draws the same screen as reaching it from the
    #: table.
    history: list[InterviewSummary]


class Account(CamelModel):
    """The caller, as `config.accounts` has them — what `GET /me` returns.

    The dashboard greets a person by name and shows what their role admits, and
    both facts live in a table only the server can read. Without this the
    greeting is an email address and the UI guesses at the role, which is the
    kind of guess that becomes a client-side authorisation check.
    """

    email: str
    role: str
    display_name: str


class PublicConfig(CamelModel):
    """What a browser needs before it can sign anybody in — `GET /config`.

    Handed down rather than baked into a bundle, which is the same argument
    `POST /session` already makes for `LIVEKIT_PUBLIC_URL`: the frontend holds
    one piece of configuration (where to send `/api`), and rotating the publishable key
    is a secret change rather than a rebuild of two static sites.

    Both values are public by construction — the publishable key is published to every
    browser that signs in, and `config.accounts` is what decides who has a
    caseload.
    """

    supabase_url: str
    supabase_publishable_key: str


class PatientSummary(CamelModel):
    """One row of the patients screen: the caller's own list, plus the
    unowned demo rows nobody was dispatched."""

    id: str
    first_name: str
    #: Seeded, and only ever from the range NHS England reserves for test data —
    #: `clinical.patients` has a CHECK that will not hold anything else. Null for
    #: a row this product created itself.
    nhs_number: str | None
    date_of_birth: date | None
    origin: PatientOrigin
    #: Null for a demo visitor — nobody was dispatched a call to them.
    clinician_email: str | None
    interview_count: int
    #: When something last happened to them — the end of their last call, or its
    #: start, or when it was queued. Null for a patient nobody has called.
    last_interview_at: datetime | None
    #: Their last finished call, which is what the row's arrow opens and what
    #: its middle column names. All three null together.
    last_interview_id: str | None
    last_protocol_id: str | None
    last_protocol_label: str | None
    #: The slot on their next call still out, if one is.
    next_scheduled_for: datetime | None
    #: What is still owed on them: calls the gate stopped, and calls that did
    #: not finish. Counted server-side because a count taken from one page of
    #: the review table changes when you turn the page.
    open_count: int
    #: Whether any of those was the gate stopping a call, which is what decides
    #: the pill is red rather than amber.
    has_escalation: bool
    created_at: datetime


# ─── dispatch ────────────────────────────────────────────────────────────────
#
# What `POST /interviews`, `POST /interviews/{id}/invitation` and `GET
# /protocols` carry, and the one optional body `POST /session` now accepts.
# Phase 5a's whole surface: a clinician queues an interview, mints a link for
# it, and a patient follows that link.

#: How the link reaches the patient. `'link'` is the only one Phase 5a sends —
#: the clinician copies the URL out of the dashboard — and `'email'` exists so
#: that a `services/comms/` later is a sender rather than a redesign.
InvitationChannel = Literal["link", "email"]


class DispatchRequest(CamelModel):
    """Queue one interview — the composer's *who · what · when*.

    Exactly one of `patient_id` and `first_name`: an existing person off the
    caller's list, or a new one. A new patient is a first name and nothing else,
    because this product collects no demographics and `docs/system-map.md`
    forbids inventing any — the identity a real deployment holds is carried
    across at dispatch from a system that already has it.
    """

    #: Someone already on the caller's list. Must be within their scope, or the
    #: request is a 404 for the same reason a read outside it is.
    patient_id: str | None = None
    #: A person nobody has called before. First name only, and it is what the
    #: assistant says out loud in the first sentence of the call.
    first_name: str | None = None
    protocol_id: str
    #: When the call is for. Null means "as soon as they follow the link" — the
    #: row is queued either way, because nothing here dials anybody.
    scheduled_for: datetime | None = None


class Invitation(CamelModel):
    """A link, as the dashboard copies it.

    The URL is assembled by the backend, not the browser, for the same reason
    `POST /session` hands down `LIVEKIT_PUBLIC_URL`: where the patient portal
    lives is deployment configuration, and a bundle that baked it in would have
    to be rebuilt to move it.
    """

    url: str
    interview_id: str
    channel: InvitationChannel
    expires_at: datetime | None


class SessionStart(CamelModel):
    """The optional body of `POST /session`.

    Every field defaults, so the body-less POST the public demo has always made
    still means "start a demo call" and nothing about the patient portal's
    existing request had to change.
    """

    #: The opaque token out of `?invite=`. Never an interview id: the id is on
    #: the clinician's screen and in the dashboard's URLs, and a link that
    #: carried it would let anyone who saw one guess the next.
    invite: str | None = None


# ─── patient experience ──────────────────────────────────────────────────────
#
# What `GET /experience` returns. No survey collects this — the rows are seeded
# by `supabase/migrations/*_experience.sql` — but the query, the scoping and the
# shape are the real ones, so the day a survey exists only the writer is new.

ExperienceRange = Literal["today", "week", "all"]


class ExperienceDay(CamelModel):
    """One bar of the chart."""

    #: Already formatted for the axis, because the window is chosen server-side
    #: and the browser has no way to know whether it is drawing weekdays or
    #: dates without being told twice.
    label: str
    positive: int
    neutral: int
    negative: int


class ExperienceSummary(CamelModel):
    """The panel, in one request.

    `scope` is the caption under the chart and it names dates rather than saying
    "today", because the window anchors on the newest response in the table
    rather than on the clock — seeded data goes stale, and a chart that claims
    to be today when it is drawing last spring is the lie this whole stage was
    about removing.
    """

    days: list[ExperienceDay]
    scope: str
