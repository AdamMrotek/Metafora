"""The record, against a real Postgres.

`tests/test_schema.py` proves the schema keeps its promises. This file proves
the code actually writes what the schema is shaped for: a claimed interview, a
transcript in order, and an outcome a clinician can read afterwards.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import asyncio

import pytest

from services.agent.config.protocol import PROTOCOLS
from services.agent.session_log import PostgresSessionWriter, RoomJoined, SafetyScanned
from services.core import db
from services.core.queue import claim, resolve_interview
from services.core.store import create_session, end_session

pytestmark = pytest.mark.postgres


@pytest.fixture
async def live_db(pool):
    """Point `services.core` at the scratch database for the length of one test."""
    db.configure(pool)
    await db.seed_protocols()
    try:
        yield pool
    finally:
        db.configure(None)


# ─── config.protocols ────────────────────────────────────────────────────────


async def test_seeding_the_protocols_twice_is_not_an_error(live_db):
    """It runs on every boot, and the table refuses UPDATE by trigger — so
    `on conflict do nothing` is the only spelling that survives a restart."""
    await db.seed_protocols()

    count = await live_db.fetchval("select count(*) from config.protocols")
    assert count == len(PROTOCOLS)


async def test_a_seeded_protocol_is_readable_back_as_the_object(live_db):
    """The column holds the whole ProtocolVersion, camelCase as on the wire, so
    a captured field can be read back against the question that produced it."""
    version = await live_db.fetchval(
        "select version from config.protocols where id = $1", "proto_warmup_v1"
    )
    assert version["id"] == "proto_warmup_v1"
    assert version["script"]["sections"][0]["questions"][0]["fieldKey"] == "day_mood"


# ─── dispatch ────────────────────────────────────────────────────────────────


async def test_resolving_attaches_to_a_roster_patient_and_claims_the_interview(live_db):
    """A visitor takes the call *as* one of the seeded ten rather than becoming
    an eleventh person. That is what keeps `clinical.patients` a caseload and
    not a visitor log (deployment.md §4, blocker 6)."""
    before = await live_db.fetchval("select count(*) from clinical.patients")
    interview = await resolve_interview()

    row = await live_db.fetchrow(
        "select i.status, i.started_at, p.origin, p.first_name, p.nhs_number, p.date_of_birth "
        "from clinical.interviews i join clinical.patients p on p.id = i.patient_id "
        "where i.id = $1",
        interview.id,
    )
    assert row["status"] == "running"
    assert row["started_at"] is not None
    # The column that lets a dashboard tell a demo visitor from a real caseload.
    assert row["origin"] == "demo"
    assert row["first_name"] == interview.patient.first_name
    # A seeded identity is what makes a demo row a roster row.
    assert row["nhs_number"].startswith("999")
    assert row["date_of_birth"] is not None
    assert await live_db.fetchval("select count(*) from clinical.patients") == before


async def test_an_interview_is_claimed_exactly_once(live_db):
    """Two tabs opened from one Phase 5 link both find the row. One gets it."""
    interview = await resolve_interview()  # already claimed by resolving

    assert await claim(interview.id) is False


async def test_two_callers_at_once_get_two_rows(live_db):
    """The roadmap's own Phase 1 acceptance line — two *interviews*.

    Two patients is no longer the property and must not be asserted: the roster
    is drawn from at random, so two visitors landing on the same person is the
    shared caseload working, not a collision. What still cannot happen is two
    callers sharing one interview.
    """
    a, b = await asyncio.gather(resolve_interview(), resolve_interview())

    rows = await live_db.fetch(
        "select id, patient_id from clinical.interviews where id = any($1::text[])",
        [a.id, b.id],
    )
    assert len(rows) == 2
    assert a.id != b.id


# ─── transcript.events ───────────────────────────────────────────────────────


async def test_the_writer_records_every_event_in_order(live_db):
    interview = await resolve_interview()
    writer = PostgresSessionWriter("s_test01", interview.id, live_db)

    writer.append(RoomJoined(identity="assistant"))
    writer.append(SafetyScanned(blocked=False, hits=[]))
    await writer.close()

    rows = await live_db.fetch(
        "select seq, type, payload from transcript.events "
        "where session_id = 's_test01' order by seq"
    )
    assert [r["seq"] for r in rows] == [1, 2]
    assert [r["type"] for r in rows] == ["room.joined", "safety.scanned"]
    # The payload is the JSONL line minus `at` and `sessionId`, which are columns.
    assert rows[0]["payload"] == {"type": "room.joined", "identity": "assistant"}
    assert rows[1]["payload"] == {"type": "safety.scanned", "blocked": False, "hits": []}


async def test_closing_the_writer_twice_is_not_an_error(live_db):
    """Both `teardown` and `_run` close it — a call ends down one path or the
    other, and neither may assume it was the first to notice."""
    interview = await resolve_interview()
    writer = PostgresSessionWriter("s_test02", interview.id, live_db)
    writer.append(RoomJoined(identity="assistant"))

    await writer.close()
    await writer.close()

    assert await live_db.fetchval(
        "select count(*) from transcript.events where session_id = 's_test02'"
    ) == 1


async def test_a_failed_write_does_not_reach_the_caller(live_db, capsys):
    """A database that goes away mid-call must not end the call. The patient is
    on the line; the write is not what they are there for. Loud, though —
    silence here would mean losing a record with nothing to show for it."""

    class Gone:
        async def executemany(self, *_args):
            raise ConnectionError("server closed the connection unexpectedly")

    writer = PostgresSessionWriter("s_test03", "iv_does_not_matter", Gone())
    writer.append(RoomJoined(identity="assistant"))

    await writer.close()  # does not raise

    assert "write failed for s_test03" in capsys.readouterr().err


# ─── the whole call ──────────────────────────────────────────────────────────


async def test_a_completed_call_records_its_outcome_and_what_it_captured(live_db):
    interview = await resolve_interview()
    protocol = PROTOCOLS[interview.protocol_id]
    session = await create_session(interview, protocol)
    session.machine.capture("day_mood", "Not bad, thanks")
    session.machine.advance()
    # Every protocol carries `CLOSING`, so the warm-up is two states, not one.
    session.machine.capture("anything_else", "No, that's everything")
    session.machine.advance()  # past the last question, so the interview is complete

    await end_session(session, "complete")
    await session.writer.close()

    row = await live_db.fetchrow(
        "select status, outcome, ended_at from clinical.interviews where id = $1", interview.id
    )
    assert row["status"] == "completed"
    assert row["outcome"] == "complete"
    assert row["ended_at"] is not None

    results = await live_db.fetch(
        "select field_key, value, status from clinical.results where interview_id = $1",
        interview.id,
    )
    assert sorted((r["field_key"], r["value"], r["status"]) for r in results) == [
        ("anything_else", "No, that's everything", "captured"),
        ("day_mood", "Not bad, thanks", "captured"),
    ]

    types = await live_db.fetch(
        "select type from transcript.events where interview_id = $1 order by seq", interview.id
    )
    assert [r["type"] for r in types] == ["session.created", "session.ended"]


async def test_the_goodbye_is_still_recorded_when_the_patient_hangs_up(live_db):
    """`teardown` runs inside the pipeline's own task when the patient
    disconnects, so its wait for the goodbye returns immediately. Closing the
    writer there would drop everything the goodbye still had to say — the close
    belongs to `_run`, which really is last."""
    interview = await resolve_interview()
    session = await create_session(interview, PROTOCOLS[interview.protocol_id])

    await end_session(session, "patient_left")
    # What the pipeline goes on to emit while it drains its EndFrame.
    session.writer.append(RoomJoined(identity="assistant"))
    await session.writer.close()

    types = await live_db.fetch(
        "select type from transcript.events where interview_id = $1 order by seq", interview.id
    )
    assert [r["type"] for r in types] == ["session.created", "session.ended", "room.joined"]


async def test_a_call_the_patient_abandoned_says_so(live_db):
    """The fourth status exists so an unfinished interview is not filed as one
    a clinician can read and sign."""
    interview = await resolve_interview()
    session = await create_session(interview, PROTOCOLS[interview.protocol_id])

    await end_session(session, "patient_left")
    await session.writer.close()

    assert await live_db.fetchval(
        "select status from clinical.interviews where id = $1", interview.id
    ) == "abandoned"
