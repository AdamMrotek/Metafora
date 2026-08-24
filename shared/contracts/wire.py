"""Messages the assistant participant sends to the browser over the LiveKit
data channel.

One transport for the whole screen: no polling, no second connection. The
browser renders what it is told and guesses nothing — in particular the call
bar reflects server phase rather than local inference, because the server is
the only party that knows whether it is still generating.

These shapes are frozen by `frontend/call/src/call/useCall.ts`, which is not
being changed by the migration. `LiveKitTransport.send_message(json)` publishes
them; the client ignores the LiveKit `topic` and simply JSON-parses the payload,
so the discriminant `t` is the whole protocol.
"""

import json
from typing import Literal

from shared.contracts.models import CallPhase, CamelModel, Clinician, FieldState


class SessionBootstrap(CamelModel):
    """Returned by `POST /session`, before the browser joins the room."""

    session_id: str
    room_name: str
    clinician: Clinician
    patient_first_name: str
    fields: list[FieldState]


class UtteranceMessage(CamelModel):
    """Append a bubble to the thread."""

    t: Literal["utterance"] = "utterance"
    who: Literal["assistant", "patient"]
    text: str
    id: str


class PhaseMessage(CamelModel):
    """Drive the call bar."""

    t: Literal["phase"] = "phase"
    phase: CallPhase


class NotesMessage(CamelModel):
    """Repaint the "Notes so far" card."""

    t: Literal["notes"] = "notes"
    fields: list[FieldState]


class EndedMessage(CamelModel):
    """The line closes on a sentence, not a submit."""

    t: Literal["ended"] = "ended"
    #: `interrupted` is a call this side cut short while healthy — a deploy,
    #: the duration ceiling. Distinct from `error` because the patient did
    #: nothing wrong and the right thing to offer them is a way back, and
    #: distinct from `complete` because the interview did not finish.
    reason: Literal["complete", "safety", "interrupted", "error"]
    say: str | None = None


ServerMessage = UtteranceMessage | PhaseMessage | NotesMessage | EndedMessage


class TypedMessage(CamelModel):
    """The patient chose to type rather than speak."""

    t: Literal["typed"] = "typed"
    text: str


ClientMessage = TypedMessage


def encode_message(message: ServerMessage) -> str:
    """Serialise for `LiveKitTransport.send_message`, which takes a string.

    `exclude_none` keeps optional fields absent rather than `null`, matching what
    the hand-written TypeScript encoder produced.
    """
    return json.dumps(message.model_dump(by_alias=True, exclude_none=True))
