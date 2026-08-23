"""Session records, held in this process.

The backend owns conversation state. It owns it by being *in* the media path
rather than by reacting to what a browser chose to relay. In-memory is honest
for phase 1: this process is already stateful because it holds live WebRTC
connections, so persisting the record without also solving mid-call restart
would buy nothing. The log on disk is the durable artefact.

`services.agent` must never import this module — see `.importlinter`. The bot
receives a `SessionWriter` closed over one patient and nothing else.
"""

import uuid
from dataclasses import dataclass, field

from services.agent.machine import InterviewMachine
from services.agent.session_log import JsonlSessionWriter, SessionCreated, SessionEnded
from shared.contracts.models import ProtocolVersion, QueuedInterview


@dataclass
class Session:
    id: str
    room_name: str
    protocol: ProtocolVersion
    interview: QueuedInterview
    machine: InterviewMachine
    writer: JsonlSessionWriter
    ended_reason: str | None = None
    ended: bool = False
    #: Set once the bot is running; `services.core` owns the handle, the bot
    #: does not own itself.
    bot: object | None = field(default=None, repr=False)


_sessions: dict[str, Session] = {}


def create_session(interview: QueuedInterview, protocol: ProtocolVersion) -> Session:
    session_id = f"s_{uuid.uuid4().hex[:8]}"
    room_name = f"intake-{session_id}"
    writer = JsonlSessionWriter(session_id)

    session = Session(
        id=session_id,
        room_name=room_name,
        protocol=protocol,
        interview=interview,
        machine=InterviewMachine(protocol),
        writer=writer,
    )
    _sessions[session_id] = session

    writer.append(
        SessionCreated(
            protocolId=protocol.id,
            patientId=interview.patient.id,
            roomName=room_name,
        )
    )
    return session


def get_session(session_id: str) -> Session | None:
    return _sessions.get(session_id)


def live_sessions() -> list[Session]:
    return [s for s in _sessions.values() if not s.ended]


def end_session(session: Session, reason: str) -> None:
    """Idempotent: a call ends once, however many things notice."""
    if session.ended:
        return
    session.ended = True
    session.ended_reason = reason
    session.writer.append(SessionEnded(reason=reason, fields=session.machine.captured))
