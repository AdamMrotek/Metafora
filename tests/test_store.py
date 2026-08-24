"""Session records.

In-memory today, Postgres-backed later. These tests are written against the
*contract* — what a session is, when the log gets written, what "ended" means —
rather than the dict, so they still hold when the record moves to a database.
"""

import json

from services.agent.config.protocol import PROTOCOLS
from services.core.queue import next_interview
from services.core.store import create_session, end_session, get_session, live_sessions


def _new():
    interview = next_interview()
    return create_session(interview, PROTOCOLS[interview.protocol_id])


def _log(session) -> list[dict]:
    return [json.loads(line) for line in session.writer.path.read_text().splitlines()]


def test_creating_a_session_writes_the_opening_record():
    session = create_session(
        interview := next_interview(), PROTOCOLS[next_interview().protocol_id]
    )

    created = _log(session)[0]
    assert created["type"] == "session.created"
    assert created["sessionId"] == session.id
    assert created["patientId"] == interview.patient.id
    assert created["roomName"] == session.room_name


def test_each_session_gets_its_own_id_and_room():
    a, b = _new(), _new()
    assert a.id != b.id
    assert a.room_name != b.room_name
    # The room name carries the session id, which is what makes a stray room in
    # the SFU traceable back to a record.
    assert a.id in a.room_name


def test_a_new_session_is_live_and_unended():
    session = _new()
    assert session.ended is False
    assert session.ended_reason is None
    assert session in live_sessions()
    assert get_session(session.id) is session


def test_ending_records_the_reason_and_what_was_captured():
    session = _new()
    end_session(session, "ended_by_patient")

    ended = _log(session)[-1]
    assert ended["type"] == "session.ended"
    assert ended["reason"] == "ended_by_patient"
    # The captured fields land on the record, not only in the live machine.
    assert ended["fields"] == session.machine.captured
    assert session not in live_sessions()


def test_a_call_ends_once_however_many_things_notice():
    """The patient, the pipeline, the watchdog and a drain all race to end it."""
    session = _new()
    end_session(session, "patient_left")
    end_session(session, "server_shutdown")

    assert session.ended_reason == "patient_left"
    assert [e["type"] for e in _log(session)].count("session.ended") == 1


def test_an_unknown_session_is_none_not_an_error():
    assert get_session("s_nope") is None
