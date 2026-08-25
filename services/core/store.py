"""Session records: the live handle here, the durable record in Postgres.

The split is the point. This process is stateful by construction — it holds the
WebRTC connection, the pipeline task and the interview machine, and none of
those survive a restart in any useful form, so they stay a process dict. What a
clinician asks questions about afterwards does not belong in that dict at all,
so `clinical.interviews`, `clinical.results` and `transcript.events` hold it and
outlive the process that wrote them.

With no database configured this degrades to exactly what it was: an in-process
dict and a JSONL file. That is a supported dev configuration, not a fallback.

`services.agent` does not import this module. The bot receives a
`SessionWriter` closed over one patient and nothing else.
"""

import uuid
from dataclasses import dataclass, field

from loguru import logger

from services.agent.machine import InterviewMachine
from services.agent.session_log import (
    JsonlSessionWriter,
    PostgresSessionWriter,
    SessionCreated,
    SessionEnded,
    SessionWriter,
)
from services.core import db
from shared.contracts.models import ProtocolVersion, QueuedInterview


@dataclass
class Session:
    id: str
    room_name: str
    protocol: ProtocolVersion
    interview: QueuedInterview
    machine: InterviewMachine
    writer: SessionWriter
    ended_reason: str | None = None
    ended: bool = False
    #: Set once the bot is running; `services.core` owns the handle, the bot
    #: does not own itself.
    bot: object | None = field(default=None, repr=False)


_sessions: dict[str, Session] = {}


async def create_session(interview: QueuedInterview, protocol: ProtocolVersion) -> Session:
    """The live handle for a call whose row `resolve_interview` already claimed."""
    session_id = f"s_{uuid.uuid4().hex[:8]}"
    room_name = f"intake-{session_id}"
    writer: SessionWriter = (
        PostgresSessionWriter(session_id, interview.id, db.pool())
        if db.enabled()
        else JsonlSessionWriter(session_id)
    )

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


async def end_session(session: Session, reason: str) -> None:
    """Idempotent: a call ends once, however many things notice."""
    if session.ended:
        return
    session.ended = True
    session.ended_reason = reason
    session.writer.append(SessionEnded(reason=reason, fields=session.machine.captured))
    await _record_outcome(session, reason)
    await session.writer.flush()


async def _record_outcome(session: Session, reason: str) -> None:
    """Close the interview row and write what the call captured.

    `clinical.results` is derivable from `transcript.events` and is written
    anyway: the review composer should not have to replay a conversation to
    render a row. `machine.fields()` is already exactly the table's shape, and
    already the same list the patient watched fill in during the call.
    """
    if not db.enabled():
        return

    fields = session.machine.fields()
    try:
        pool = db.pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "update clinical.interviews "
                "set status = $2, ended_at = now(), outcome = $3 where id = $1",
                session.interview.id,
                "completed" if session.machine.complete else "abandoned",
                reason,
            )
            await conn.executemany(
                "insert into clinical.results "
                "(interview_id, field_key, label, value, status) "
                "values ($1, $2, $3, $4, $5) "
                "on conflict (interview_id, field_key) do update set "
                "value = excluded.value, status = excluded.status, updated_at = now()",
                [(session.interview.id, f.key, f.label, f.value, f.status) for f in fields],
            )
    except Exception as exc:
        # The call is already over; losing the outcome must not turn a completed
        # interview into a stack trace on the way out. The transcript rows are
        # written by their own path and are enough to rebuild this.
        logger.warning(f"[store] could not record the outcome of {session.interview.id}: {exc}")
