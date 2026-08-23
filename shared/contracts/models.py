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

from typing import Annotated, Literal

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
