"""The session log.

Because every transcript and every reply passes through this process, the log
is written *from the source* rather than reconstructed from whatever the
browser chose to relay. That is the audit trail: both sides of the
conversation, every state transition, every red-flag decision, and the latency
of every turn. Append-only JSONL keeps it greppable and defers the database
decision. The interface is the point — swapping in SQLite or Postgres later is
one new implementation, not a migration of the pipeline.

Raw audio is deliberately not retained: transcripts and state transitions only.
Whether that changes is a DPIA decision, and not retaining is the reversible
default.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field

# ─── The typed event union ───────────────────────────────────────────────────
# Ported unchanged from `session/log.ts`. Neither Pipecat's OTel spans nor its
# metrics frames are this artefact; they are telemetry about the system, and
# this is a record of the conversation. Do not let a metrics story replace it.


class _Event(BaseModel):
    pass


class SessionCreated(_Event):
    type: Literal["session.created"] = "session.created"
    protocolId: str
    patientId: str
    roomName: str


class RoomJoined(_Event):
    type: Literal["room.joined"] = "room.joined"
    identity: str


class PatientJoined(_Event):
    type: Literal["patient.joined"] = "patient.joined"
    identity: str


class TurnCommitted(_Event):
    type: Literal["turn.committed"] = "turn.committed"
    transcript: str
    durationMs: float
    source: Literal["voice", "typed"]


class OpeningSpoken(_Event):
    type: Literal["opening.spoken"] = "opening.spoken"
    text: str
    chunks: int
    ms: float


class SafetyScanned(_Event):
    type: Literal["safety.scanned"] = "safety.scanned"
    blocked: bool
    hits: list[str]
    action: str | None = None


class LlmCompleted(_Event):
    type: Literal["llm.completed"] = "llm.completed"
    text: str
    toolCalls: int


class ToolCalled(_Event):
    type: Literal["tool.called"] = "tool.called"
    name: str
    args: object
    authorised: bool
    reason: str | None = None


class TtsSpoken(_Event):
    type: Literal["tts.spoken"] = "tts.spoken"
    chars: int
    chunks: int


class StateTransition(_Event):
    type: Literal["state.transition"] = "state.transition"
    # `from` is a Python keyword; the wire name is preserved by alias.
    from_: str = Field(alias="from")
    to: str


class TurnAborted(_Event):
    type: Literal["turn.aborted"] = "turn.aborted"
    reason: Literal["barge_in"] = "barge_in"
    discardedText: str


class LatencyTurn(_Event):
    type: Literal["latency.turn"] = "latency.turn"
    ms: dict[str, float]


class EndpointDecision(_Event):
    """Measurement, not policy — added by the migration, not ported.

    `tuning.py` argues for a 700 ms floor before we commit a turn; Pipecat 1.7
    ends turns with a semantic model instead. Rather than assert which is right
    for a patient who trails off mid-sentence, every turn records what actually
    happened and what the floor would have done, so the argument can be settled
    on real calls instead of in a design document.
    """

    type: Literal["endpoint.decision"] = "endpoint.decision"
    #: Silence the patient actually sat through before the turn was committed.
    silenceMs: float
    #: What ended it. Today always the turn analyser.
    decidedBy: str
    #: The floor `tuning.ENDPOINT_SILENCE_MS` would have imposed.
    floorMs: float
    #: True when the floor would have waited longer than the model did.
    floorWouldHaveWaited: bool


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    where: str
    message: str


class SessionEnded(_Event):
    type: Literal["session.ended"] = "session.ended"
    reason: str
    fields: dict[str, str | None]


LogEvent = Annotated[
    SessionCreated
    | RoomJoined
    | PatientJoined
    | TurnCommitted
    | OpeningSpoken
    | SafetyScanned
    | LlmCompleted
    | ToolCalled
    | TtsSpoken
    | StateTransition
    | TurnAborted
    | LatencyTurn
    | EndpointDecision
    | ErrorEvent
    | SessionEnded,
    Field(discriminator="type"),
]


# ─── The write boundary (§8) ─────────────────────────────────────────────────


class SessionWriter(Protocol):
    """The whole of what the bot knows about storage.

    Note what is missing: there is no patient id parameter anywhere. A writer is
    constructed per session by `services.core` with the id closed over, and the
    pipeline holds it for the length of the call. The bot cannot name a
    different patient because it has no way to spell one.

    TypeScript enforced this for free — `services/agent` simply would not have
    listed the store in its `package.json`. Python has no equivalent, so the
    boundary is declared here and checked by import-linter in CI. We noticed
    the language would not enforce it and did not assume it away.

    `note_end_reason` is the other side of the same coin: when the bot ends the
    call itself (interview complete, safety closure) the store — not the bot —
    owns the session record, so the reason crosses the boundary on the writer,
    and `services.core` reads it back when the pipeline goes quiet.
    """

    def append(self, event: LogEvent) -> None: ...

    def note_end_reason(self, reason: str) -> None:
        """Declare how this call is ending, from inside the pipeline.

        The store records it; the bot may not reach the store directly (§8).
        The default is the silent no-op the append-only contract always was:
        a writer that never heard the reason behaves exactly as it did before.
        """

        ...


class JsonlSessionWriter:
    """Append-only JSONL at `logs/<sessionId>.jsonl`."""

    def __init__(self, session_id: str, log_dir: Path | None = None) -> None:
        self._session_id = session_id
        directory = log_dir or Path(__file__).resolve().parents[2] / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / f"{session_id}.jsonl"
        #: How the bot says the call ended, when the bot ended it. `None` when
        #: the call ends from outside the pipeline (patient left, server drain).
        self.ending_reason: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def note_end_reason(self, reason: str) -> None:
        """The bot declares how the call ended before the pipeline goes quiet.

        Append-only on purpose: the reason is not an event in the log (the
        store writes `SessionEnded` with the final reason), it is a flag the
        store consults. `services.core` prefers its own `ended_reason`; this is
        the fallback for pipeline-initiated ends.
        """
        self.ending_reason = reason

    def append(self, event: LogEvent) -> None:
        line = {
            "at": datetime.now(UTC).isoformat(),
            "sessionId": self._session_id,
            **event.model_dump(by_alias=True, exclude_none=True),
        }
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        except OSError as exc:
            # A failed write must never take down a live call, but it must be loud.
            print(f"[session-log] write failed for {self._session_id}: {exc}", file=sys.stderr)


def summarise(event: LogEvent) -> str:
    """Console-only formatting. Never the record — that is the JSONL."""
    if isinstance(event, SafetyScanned):
        return f"safety: {'BLOCKED ' + ','.join(event.hits) if event.blocked else 'clean'}"
    if isinstance(event, ToolCalled):
        return f"tool: {event.name} {'ok' if event.authorised else f'REFUSED ({event.reason})'}"
    if isinstance(event, LatencyTurn):
        return "latency: " + " ".join(f"{k}={v:.0f}ms" for k, v in event.ms.items())
    return event.type
