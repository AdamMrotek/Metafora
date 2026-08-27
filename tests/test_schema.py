"""The store schema, exercised against a real Postgres.

Two of these tables carry a promise that no amount of careful calling can keep:
`config.protocols` is immutable so a captured field can always be read back
against the question that produced it, and `transcript.events` is append-only
because the record of a call does not change. Both are enforced by trigger, and
a trigger is exactly the kind of thing a later migration drops by accident —
so it is asserted here rather than assumed.

Phase 2 adds a third of the same kind. `clinical.patients.clinician_email`
references `config.accounts`, which is what stops a caseload being assigned to
an address nobody granted.

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


# ─── config.accounts ─────────────────────────────────────────────────────────


async def test_the_migration_seeds_an_account_to_sign_in_as(db):
    """Seeded by the migration, never by the application: signing up must not be
    the same act as being granted a caseload, which on a public demo URL is
    exactly what self-serve would mean. A deployment whose seed is empty has no
    way in at all, so the row is asserted rather than assumed."""
    seeded = await db.fetch(
        "select email, role from config.accounts where role in ('clinician', 'admin')"
    )

    assert seeded, "the migration seeds at least one account with a caseload"


# `sub` starts null and is bound on first verified sign-in — asserted in
# `tests/test_reads.py`, against the code that does the binding, rather than
# here: the scratch database is shared across the postgres suite and only the
# rows a test names are its own.


async def test_an_account_may_not_hold_a_role_the_door_does_not_know(db):
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "insert into config.accounts (email, role, display_name) "
            "values ('nobody@example.test', 'superuser', 'Nobody')"
        )


async def test_two_accounts_cannot_share_one_auth_user(db):
    """`sub` is bound the first time a verified token arrives. Two rows holding
    the same one would mean a single sign-in resolving to two caseloads."""
    await db.execute(
        "insert into config.accounts (email, role, display_name, sub) "
        "values ('a@schema.test', 'clinician', 'A', 'auth-user-schema-1')"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "insert into config.accounts (email, role, display_name, sub) "
            "values ('b@schema.test', 'clinician', 'B', 'auth-user-schema-1')"
        )


async def test_a_patient_cannot_be_owned_by_an_account_nobody_granted(db):
    """The column is a foreign key, not a free-text email, so a dispatch cannot
    assign a caseload to an address the seed never named."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "insert into clinical.patients (id, first_name, origin, clinician_email) "
            "values ('pt_orphan', 'Ada', 'dispatched', 'ghost@example.test')"
        )


async def test_a_demo_patient_has_no_owner(db):
    """Nullable on purpose: a visitor to the public demo is nobody's patient,
    and Phase 5's dispatch is the only thing that ever sets this."""
    await db.execute("insert into clinical.patients (id, first_name) values ('pt_demo', 'Ben')")

    row = await db.fetchrow("select origin, clinician_email from clinical.patients "
                            "where id = 'pt_demo'")
    assert row["origin"] == "demo"
    assert row["clinician_email"] is None
