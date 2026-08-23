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

    @property
    def current(self) -> CompiledState | None:
        return self.states[self._index] if self._index < len(self.states) else None

    @property
    def complete(self) -> bool:
        return self._index >= len(self.states)

    def advance(self) -> CompiledState | None:
        """Advance past the current question. Returns the state we moved to."""
        self._index = min(self._index + 1, len(self.states))
        return self.current

    def authorise(self, tool_name: str) -> ToolAuthorisation:
        """A tool call is legal only from a state the matrix names. This runs before
        dispatch, in our process, on a call the model cannot route around.
        """
        spec = next((t for t in self.protocol.tools if t.name == tool_name), None)
        if spec is None:
            return ToolAuthorisation(False, "tool not in protocol")

        state = self.current
        if state is None:
            return ToolAuthorisation(False, "interview is complete")
        if state.id not in spec.allowed_states:
            return ToolAuthorisation(False, f"not allowed from {state.id}")
        return ToolAuthorisation(True)

    def capture(self, field_key: str, value: str) -> bool:
        """Write a captured value, if the key is one this protocol declares."""
        known = any(s.question.field_key == field_key for s in self.states)
        if not known:
            return False
        self._values[field_key] = value
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

    def tool_definitions(self) -> list[dict]:
        """The tool schema handed to the model, derived from the field catalog.

        The `enum` closure is load-bearing: the model cannot name a field the
        protocol does not declare.
        """
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "field": {
                            "type": "string",
                            "enum": [s.question.field_key for s in self.states],
                            "description": "The field key being recorded.",
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "What the patient said, in their own words where possible."
                            ),
                        },
                    },
                    "required": ["field", "value"],
                    "additionalProperties": False,
                },
            }
            for spec in self.protocol.tools
        ]
