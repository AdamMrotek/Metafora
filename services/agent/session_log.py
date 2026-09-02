"""The session log.

Because every transcript and every reply passes through this process, the log
is written *from the source* rather than reconstructed from whatever the
browser chose to relay. That is the audit trail: both sides of the
conversation, every state transition, every red-flag decision, and the latency
of every turn.

The interface was the point, and it paid: Phase 1 added Postgres as one new
implementation of `SessionWriter` and changed nothing in the pipeline. Which
one a call gets is decided by `services.core.store` from whether a database is
configured — Postgres when there is one, JSONL when there is not, never both.

This log holds transcripts and state transitions. Audio capture is a separate
concern and is not wired up here; if clinical-research requirements call for
retained recordings, they belong in their own store, not in this file.
"""

import asyncio
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


class ConcernRaised(_Event):
    """What a question's own flags made of one answer.

    Deliberately the same shape as `SafetyScanned` where the record reads it —
    `hits` and `action` — because the escalation band, `flag_count` and
    `worst_flag` are one question asked of the whole call and must not need two
    answers. Everything else here is provenance, and the reason this is its own
    event type rather than a second `safety.scanned`: the gate cannot be talked
    out of a match and this can, so a clinician is owed the difference between
    a flag the words tripped and one a model proposed.

    Written on **every** capture, including the ones that raise nothing — for
    the same reason a scan that matched nothing is still written. An event only
    where something fired is a record that cannot show the question was looked at.
    """

    type: Literal["concern.raised"] = "concern.raised"
    #: The field the answer landed in — which is what says *which question*.
    field: str
    #: Every flag raised, whichever trigger raised it. Unnested by `reads.py`.
    hits: list[str]
    #: The worst action across `hits`, ranked by `safety.SEVERITY`.
    action: str | None = None
    #: The enum member the model classified the answer into, if the
    #: question declared any. The `value` trigger's whole input.
    answer: str | None = None
    #: The subset of `hits` a table lookup produced, and the subset the model
    #: named. Both are in `hits`; these say which net caught it.
    matched: list[str] = []
    judged: list[str] = []
    #: A flag the model named that this question does not declare. Dropped, and
    #: recorded — a refusal nobody can see is indistinguishable from an
    #: authorisation.
    ignored: str | None = None


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
    | ConcernRaised
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

    `note_end_reason` is the other side of the same coin: when the bot ends the
    call itself (interview complete, safety closure) the store — not the bot —
    owns the session record, so the reason crosses the boundary on the writer,
    and `services.core` reads it back when the pipeline goes quiet.
    """

    def append(self, event: LogEvent) -> None: ...

    async def flush(self) -> None:
        """Block until everything appended so far is durable.

        Free for a writer that was never buffering; the point of the method is
        that `services.core` does not have to know which kind it holds.
        """

        ...

    async def close(self) -> None:
        """Release whatever the writer was holding. Idempotent."""

        ...

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

    async def flush(self) -> None:
        """Nothing to do: `append` closed the file before it returned."""

    async def close(self) -> None:
        """Nothing to hold open: the file is opened and closed per line."""

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
class PostgresSessionWriter:
    """Append-only rows in `transcript.events`, one per line the JSONL would write.

    `append` is called from the pipeline's own task on every turn — six sites in
    `observer.py` alone — so it must not touch the network. A round trip to a
    hosted Postgres on each event would land in the same 32 ms budget Silero and
    SmartTurn are already spending, and the patient would hear it. So `append`
    only stamps a sequence number and hands the event to a queue; a background
    task writes batches.

    The cost of that is the last few events being in flight when a call ends,
    which is what `flush` and `close` are for: the store flushes when it writes
    `SessionEnded`, and `app.py` closes once the goodbye has actually been said.
    """

    #: A wedged pool must not hold a shutdown open. Long enough that an ordinary
    #: round trip always wins, short enough that a drain still completes.
    FLUSH_TIMEOUT_S = 5.0

    _INSERT = (
        "insert into transcript.events "
        "(interview_id, session_id, seq, type, at, payload) "
        "values ($1, $2, $3, $4, $5, $6) "
        # A retried batch is harmless. `do nothing` also never fires
        # `events_immutable`, which refuses UPDATE outright.
        "on conflict (session_id, seq) do nothing"
    )

    def __init__(self, session_id: str, interview_id: str, pool) -> None:
        self._session_id = session_id
        self._interview_id = interview_id
        self._pool = pool
        self._seq = 0
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._drain())
        self._closed = False
        #: How the bot says the call ended, when the bot ended it. Read back by
        #: `services.core`; see `JsonlSessionWriter.note_end_reason`.
        self.ending_reason: str | None = None

    def note_end_reason(self, reason: str) -> None:
        self.ending_reason = reason

    def append(self, event: LogEvent) -> None:
        if self._closed:
            # Nothing would drain it. Losing a line of the record is worth a
            # complaint even though it must not be worth an exception.
            print(
                f"[session-log] {event.type} appended after close for {self._session_id}",
                file=sys.stderr,
            )
            return
        self._seq += 1
        self._queue.put_nowait(
            (
                self._interview_id,
                self._session_id,
                self._seq,
                event.type,
                datetime.now(UTC),
                # Exactly the JSONL line minus `at` and `sessionId`, which are
                # columns here. `type` stays in the payload as well as being
                # lifted out, so a row and a log line say the same thing.
                event.model_dump(by_alias=True, exclude_none=True),
            )
        )

    async def flush(self) -> None:
        """Wait until everything appended so far is on disk in Postgres."""
        if self._task.done():
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=self.FLUSH_TIMEOUT_S)
        except TimeoutError:
            print(
                f"[session-log] flush timed out for {self._session_id} — "
                f"{self._queue.qsize()} event(s) unwritten",
                file=sys.stderr,
            )

    async def close(self) -> None:
        """Idempotent: called from both paths a call can end on."""
        if self._closed:
            return
        self._closed = True
        await self.flush()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _drain(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await self._pool.executemany(self._INSERT, batch)
            except Exception as exc:
                # Same rule as the JSONL writer: a failed write must never take
                # down a live call, but it must be loud.
                print(
                    f"[session-log] write failed for {self._session_id}: {exc}",
                    file=sys.stderr,
                )
            finally:
                for _ in batch:
                    self._queue.task_done()


def summarise(event: LogEvent) -> str:
    """Console-only formatting. Never the record — that is the JSONL."""
    if isinstance(event, SafetyScanned):
        return f"safety: {'BLOCKED ' + ','.join(event.hits) if event.blocked else 'clean'}"
    if isinstance(event, ToolCalled):
        return f"tool: {event.name} {'ok' if event.authorised else f'REFUSED ({event.reason})'}"
    if isinstance(event, LatencyTurn):
        return "latency: " + " ".join(f"{k}={v:.0f}ms" for k, v in event.ms.items())
    return event.type
