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

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Python reads snake_case; the wire and the generated TypeScript stay camelCase.

    `frontend/call` was written against hand-authored TypeScript and must not
    notice that the source of truth moved to Python, so serialisation aliases
    rather than a rename is the whole compatibility story.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

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


class Question(CamelModel):
    id: str
    #: Spoken to the patient. The only block a patient hears in full.
    ask: str
    #: Where the answer lands in the record, and on the clinician's composer.
    field_key: str
    #: Human label for the "Notes so far" card and the review composer row.
    label: str
    capture: Capture
    #: What to do when the answer does not resolve. Drives the follow-up budget.
    if_unclear: str | None = None
    #: A section may be skipped by policy; a question may not.
    must_capture: bool


class Section(CamelModel):
    id: str
    title: str
    questions: list[Question]


class InterviewScript(CamelModel):
    sections: list[Section]


# ─── Block II · safety break ─────────────────────────────────────────────────

RedFlagAction = Literal["end_call", "urgent_escalate", "soft_review", "note_only"]


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
    last_interview_at: datetime | None
    created_at: datetime


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
