"""The store schema, exercised against a real Postgres.

Two of these tables carry a promise that no amount of careful calling can keep:
`config.protocols` is immutable so a captured field can always be read back
against the question that produced it, and `transcript.events` is append-only
because the record of a call does not change. Both are enforced by trigger, and
a trigger is exactly the kind of thing a later migration drops by accident —
so it is asserted here rather than assumed.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import asyncpg
import pytest

pytestmark = pytest.mark.postgres


async def seed(db):
    """The minimum chain a call needs: a protocol, a patient, an interview."""
    await db.execute(
        "insert into config.protocols (id, label, version) "
        "values ('pv_1', 'Chest pain v1', '{\"id\": \"pv_1\"}')"
    )
    await db.execute("insert into clinical.patients (id, first_name) values ('pt_1', 'Ada')")
    await db.execute(
        "insert into clinical.interviews (id, protocol_id, patient_id) "
        "values ('iv_0001', 'pv_1', 'pt_1')"
    )


async def test_schemas_exist(db):
    """Four schemas, including `metrics` — claimed now so Phase 6 does not have
    to migrate around whatever else took the name."""
    rows = await db.fetch(
        "select nspname from pg_namespace "
        "where nspname in ('clinical', 'transcript', 'config', 'metrics')"
    )
    assert {r["nspname"] for r in rows} == {"clinical", "transcript", "config", "metrics"}


async def test_a_call_can_be_recorded(db):
    """The insert order the agent actually follows, end to end."""
    await seed(db)
    await db.execute(
        "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
        "values ('iv_0001', 'sess_abc', 1, 'turn', now(), '{\"role\": \"agent\"}')"
    )
    row = await db.fetchrow("select * from transcript.events where session_id = 'sess_abc'")
    assert row["interview_id"] == "iv_0001"
    assert row["payload"] == '{"role": "agent"}'


@pytest.mark.parametrize(
    "sql",
    [
        "update config.protocols set label = 'edited' where id = 'pv_1'",
        "delete from config.protocols where id = 'pv_1'",
    ],
)
async def test_a_published_protocol_cannot_be_changed(db, sql):
    await seed(db)
    with pytest.raises(Exception, match="append-only"):
        await db.execute(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "update transcript.events set type = 'edited' where session_id = 'sess_abc'",
        "delete from transcript.events where session_id = 'sess_abc'",
    ],
)
async def test_the_record_of_a_call_cannot_be_changed(db, sql):
    await seed(db)
    await db.execute(
        "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
        "values ('iv_0001', 'sess_abc', 1, 'turn', now(), '{}')"
    )
    with pytest.raises(Exception, match="append-only"):
        await db.execute(sql)


async def test_an_event_needs_an_interview(db):
    """Phase 5's dispatch has to create the interview before the call logs —
    this is the constraint that makes forgetting it a failure, not a silent
    orphan row."""
    await seed(db)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
            "values ('iv_missing', 'sess_abc', 1, 'turn', now(), '{}')"
        )


async def test_a_replayed_event_is_refused(db):
    """(session_id, seq) is unique, so a writer that retries cannot double-log."""
    await seed(db)
    insert = (
        "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
        "values ('iv_0001', 'sess_abc', 1, 'turn', now(), '{}')"
    )
    await db.execute(insert)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(insert)


@pytest.mark.parametrize(
    "sql",
    [
        "insert into clinical.interviews (id, protocol_id, patient_id, status) "
        "values ('iv_bad', 'pv_1', 'pt_1', 'sideways')",
        "insert into clinical.results (interview_id, field_key, label, status) "
        "values ('iv_0001', 'onset', 'Onset', 'guessing')",
    ],
)
async def test_status_columns_reject_a_value_the_code_never_writes(db, sql):
    await seed(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(sql)


async def test_the_queue_read_uses_its_index(db):
    """`interviews_queued` is partial and ordered to match next_interview()
    exactly; a change to either that breaks the pairing shows up here as a
    sequential scan long before it shows up as latency."""
    await seed(db)
    # Enough rows, and mostly non-queued, that a sequential scan is the wrong
    # plan on the merits — otherwise the planner picks either at this size and
    # the assertion means nothing.
    await db.executemany(
        "insert into clinical.interviews (id, protocol_id, patient_id, status) "
        "values ($1, 'pv_1', 'pt_1', $2)",
        [(f"iv_{n:05d}", "queued" if n % 50 == 0 else "completed") for n in range(2, 2000)],
    )
    await db.execute("analyze clinical.interviews")
    plan = await db.fetchval(
        "explain (format json) "
        "select id from clinical.interviews where status = 'queued' "
        "order by scheduled_for nulls first, created_at limit 1"
    )
    assert "interviews_queued" in plan
