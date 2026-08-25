"""Session records, with no database configured.

That is a real configuration, not a stand-in: `make dev` on a laptop with
nothing provisioned takes exactly this path, and the writer it gets is the JSONL
one. These tests are written against the *contract* — what a session is, when
the log gets written, what "ended" means — which is why they survived the record
moving to Postgres unchanged apart from the `await`. The Postgres half is
`tests/test_persistence.py`.
"""

import json

from services.agent.config.protocol import PROTOCOLS
from services.core.queue import synthetic_interview
from services.core.store import create_session, end_session, get_session, live_sessions


async def _new():
    interview = synthetic_interview()
    return await create_session(interview, PROTOCOLS[interview.protocol_id])


def _log(session) -> list[dict]:
    return [json.loads(line) for line in session.writer.path.read_text().splitlines()]


async def test_creating_a_session_writes_the_opening_record():
    session = await create_session(
        interview := synthetic_interview(), PROTOCOLS[synthetic_interview().protocol_id]
    )

    created = _log(session)[0]
    assert created["type"] == "session.created"
    assert created["sessionId"] == session.id
    assert created["patientId"] == interview.patient.id
    assert created["roomName"] == session.room_name


async def test_each_session_gets_its_own_id_and_room():
    a, b = await _new(), await _new()
    assert a.id != b.id
    assert a.room_name != b.room_name
    # The room name carries the session id, which is what makes a stray room in
    # the SFU traceable back to a record.
    assert a.id in a.room_name


async def test_a_new_session_is_live_and_unended():
    session = await _new()
    assert session.ended is False
    assert session.ended_reason is None
    assert session in live_sessions()
    assert get_session(session.id) is session


async def test_ending_records_the_reason_and_what_was_captured():
    session = await _new()
    await end_session(session, "ended_by_patient")

    ended = _log(session)[-1]
    assert ended["type"] == "session.ended"
    assert ended["reason"] == "ended_by_patient"
    # The captured fields land on the record, not only in the live machine.
    assert ended["fields"] == session.machine.captured
    assert session not in live_sessions()


async def test_a_call_ends_once_however_many_things_notice():
    """The patient, the pipeline, the watchdog and a drain all race to end it."""
    session = await _new()
    await end_session(session, "patient_left")
    await end_session(session, "server_shutdown")

    assert session.ended_reason == "patient_left"
    assert [e["type"] for e in _log(session)].count("session.ended") == 1


async def test_an_unknown_session_is_none_not_an_error():
    assert get_session("s_nope") is None
