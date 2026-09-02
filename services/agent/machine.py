"""Compiles a published ProtocolVersion into the things the runtime actually
executes.

The studio's handover table is the spec for this file:
  · the interview script becomes the conversation state machine and the
    field catalog;
  · the tool table becomes a permission matrix checked in-process before
    dispatch.

Four of the six blocks never enter the prompt. Only the script does.
"""

from dataclasses import dataclass

from shared.contracts.models import FieldState, ProtocolVersion, Question


@dataclass(frozen=True)
class CompiledState:
    #: `sectionId.questionId` — the id the tool matrix is keyed on.
    id: str
    section_id: str
    section_title: str
    question: Question


@dataclass(frozen=True)
class ToolAuthorisation:
    authorised: bool
    reason: str | None = None


class InterviewMachine:
    def __init__(self, protocol: ProtocolVersion) -> None:
        self.protocol = protocol
        self.states: list[CompiledState] = [
            CompiledState(
                id=f"{section.id}.{question.id}",
                section_id=section.id,
                section_title=section.title,
                question=question,
            )
            for section in protocol.script.sections
            for question in section.questions
        ]
        if not self.states:
            raise ValueError("protocol has no questions")

        self._index = 0
        self._values: dict[str, str] = {}

        # A capture has to have a patient turn behind it; these are how that is
        # known. See `note_turn` and `authorise`.
        self._turns = 0
        self._captured_at_turn = 0

    @property
    def current(self) -> CompiledState | None:
        return self.states[self._index] if self._index < len(self.states) else None

    @property
    def complete(self) -> bool:
        return self._index >= len(self.states)

    def note_turn(self) -> None:
        """A committed patient turn, counted once, before either model sees it.

        `SafetyGate` is what calls this. It is the single point every transcript
        crosses on its way to the two contexts, and it is already the arbiter of
        which turns are real — a blocked turn is swallowed there and never
        reaches here, so it can never license a capture either.
        """
        self._turns += 1

    def advance(self) -> CompiledState | None:
        """Advance past the current question. Returns the state we moved to."""
        self._index = min(self._index + 1, len(self.states))
        return self.current

    def authorise(self, tool_name: str) -> ToolAuthorisation:
        """A tool call is legal only from a state the matrix names, and only with
        a patient turn behind it. This runs before dispatch, in our process, on a
        call the model cannot route around.

        The turn condition is not a second guess at the model's judgement, it is
        the one thing the model cannot check for itself: a capture pass re-run on
        a context it has already answered has no new utterance to draw on, and it
        will either record the previous turn against the next field or invent
        one. Both happened on `iv_eca23eefda25`, and the invented one walked the
        machine into `complete` and hung up on a patient mid-question. A field is
        something the patient said; if they have not spoken since the last thing
        recorded, there is nothing to record.
        """
        spec = next((t for t in self.protocol.tools if t.name == tool_name), None)
        if spec is None:
            return ToolAuthorisation(False, "tool not in protocol")

        state = self.current
        if state is None:
            return ToolAuthorisation(False, "interview is complete")
        if state.id not in spec.allowed_states:
            return ToolAuthorisation(False, f"not allowed from {state.id}")
        if self._turns <= self._captured_at_turn:
            return ToolAuthorisation(False, "no patient turn since the last capture")
        return ToolAuthorisation(True)

    def capture(self, field_key: str, value: str) -> bool:
        """Write a captured value, if the key is one this protocol declares."""
        known = any(s.question.field_key == field_key for s in self.states)
        if not known:
            return False
        self._values[field_key] = value
        self._captured_at_turn = self._turns
        return True

    @property
    def captured(self) -> dict[str, str | None]:
        return {s.question.field_key: self._values.get(s.question.field_key) for s in self.states}

    def fields(self) -> list[FieldState]:
        """The "Notes so far" card, which is the same list as the clinician's review
        composer. A patient watching it fill in is watching the record being
        written, which is why it is worth a quarter of the screen.
        """
        out: list[FieldState] = []
        for i, s in enumerate(self.states):
            value = self._values.get(s.question.field_key)
            if value:
                status = "captured"
            elif i == self._index:
                status = "live"
            elif i < self._index:
                status = "open"  # moved past without an answer
            else:
                status = "pending"
            out.append(
                FieldState(
                    key=s.question.field_key,
                    label=s.question.label,
                    value=value,
                    status=status,
                )
            )
        return out

    def answer_values(self) -> list[str]:
        """Every value any question in this protocol declares an `EnumCapture`
        for, in script order and de-duplicated. The `answer` argument's enum.

        One list for the whole protocol rather than one per state, exactly as
        `field` is: the schema is compiled once, before the call, and the state
        is checked in `dispatch` where it is actually known.
        """
        seen: dict[str, None] = {}
        for state in self.states:
            capture = state.question.capture
            if capture.type == "enum":
                for value in capture.values:
                    seen[value] = None
        return list(seen)

    def flag_ids(self) -> list[str]:
        """Every question flag this protocol authors. The `flag` argument's enum."""
        return [f.id for s in self.states for f in s.question.flags]

    def tool_definitions(self) -> list[dict]:
        """The tool schema handed to the model, derived from the field catalog.

        The `enum` closure is load-bearing: the model cannot name a field the
        protocol does not declare — and, since the question flags, it cannot
        name a concern or an answer value the protocol does not declare either.

        Both of those arguments appear **only when the protocol authored the
        thing they are about**. A script with no enum captures and no question
        flags compiles to exactly the schema it always did, so nothing is asked
        of a model that has nothing to answer with.
        """
        properties: dict[str, dict] = {
            "field": {
                "type": "string",
                "enum": [s.question.field_key for s in self.states],
                "description": "The field key being recorded.",
            },
            "value": {
                "type": "string",
                "description": "What the patient said, in their own words where possible.",
            },
        }
        required = ["field", "value"]

        values = self.answer_values()
        if values:
            # Classification, not judgement — and deliberately the narrower ask.
            # A closed enum is something a model is reliable at; "is this
            # concerning" is not. `value` still carries their own words, because
            # the record is what they said and this is only how it resolves.
            properties["answer"] = {
                "type": "string",
                "enum": values,
                "description": (
                    "Which of this question's declared values the answer amounts to. "
                    "Omit for a question that declares none."
                ),
            }

        flags = self.flag_ids()
        if flags:
            # `none` rather than an omitted argument, and required, so that not
            # raising anything is a thing the model *said* rather than a thing
            # it forgot. `dispatch` still treats an absent one as `none`:
            # losing a captured field to a missing argument would be a worse
            # failure than losing a flag.
            properties["flag"] = {
                "type": "string",
                "enum": [*flags, "none"],
                "description": (
                    "The id of a concern this answer raises, or `none`. Raise one only "
                    "when the answer meets the condition that concern describes."
                ),
            }
            required.append("flag")

        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
            for spec in self.protocol.tools
        ]
