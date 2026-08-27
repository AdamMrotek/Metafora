"""The read surface, against a real Postgres.

Two things live here rather than in `tests/test_auth.py`, and both need a
database to mean anything: `PostgresAccounts`, whose whole job is the
`config.accounts` row and the `sub` it binds on first sign-in, and the scoping
in `reads.py` — which is SQL, so a fake pool would be asserting on a string.

The one that matters most is at the bottom: a clinician does not see another
clinician's patient. Deselected by default; `make test-pg` supplies
TEST_DATABASE_URL.
"""

import pytest

from services.agent.config.protocol import PROTOCOLS
from services.core import db, reads
from services.core.queue import resolve_interview
from services.core.store import create_session, end_session
from shared.auth import CurrentUser, Denied, PostgresAccounts
from shared.auth.verify import Claims

pytestmark = pytest.mark.postgres

ALICE = "alice@example.test"
BOB = "bob@example.test"


def user(email: str = ALICE, role: str = "clinician") -> CurrentUser:
    return CurrentUser(email=email, sub="auth-user", role=role, display_name="Test")


def claims(email: str = ALICE, sub: str = "auth-user-1") -> Claims:
    return Claims(sub=sub, email=email, raw={})


@pytest.fixture
async def live_db(pool):
    """Point `services.core` at the scratch database, with two clinicians seeded.

    Two, not one: every scoping assertion in this file is about the difference
    between them, and a fixture with a single account could not fail the test
    that matters.
    """
    db.configure(pool)
    await db.seed_protocols()
    await pool.executemany(
        "insert into config.accounts (email, role, display_name) values ($1, 'clinician', $2) "
        "on conflict (email) do nothing",
        [(ALICE, "Dr Alice"), (BOB, "Dr Bob")],
    )
    try:
        yield pool
    finally:
        db.configure(None)


async def a_patient_of(pool, email: str | None, *, first_name: str = "Nadia") -> str:
    """One dispatched patient, owned by `email` — or unowned, which is what a
    demo visitor is."""
    patient_id = f"pt_{first_name.lower()}_{abs(hash(email)) % 10**6}"
    await pool.execute(
        "insert into clinical.patients (id, first_name, origin, clinician_email) "
        "values ($1, $2, $3, $4) on conflict (id) do nothing",
        patient_id,
        first_name,
        "demo" if email is None else "dispatched",
        email,
    )
    return patient_id


async def an_interview_for(pool, patient_id: str) -> str:
    interview_id = f"iv_{patient_id}"
    await pool.execute(
        "insert into clinical.interviews (id, protocol_id, patient_id) values ($1, $2, $3) "
        "on conflict (id) do nothing",
        interview_id,
        "proto_warmup_v1",
        patient_id,
    )
    return interview_id


# ─── config.accounts ─────────────────────────────────────────────────────────


async def test_a_seeded_account_resolves_to_who_the_migration_said(live_db):
    resolved = await PostgresAccounts(db.pool).resolve(claims())

    assert resolved.email == ALICE
    assert resolved.role == "clinician"
    assert resolved.display_name == "Dr Alice"


async def test_a_verified_stranger_is_denied_rather_than_created(live_db):
    """Anyone may complete a Supabase sign-up. Being granted a caseload is a row
    the repo wrote, and nothing in the application ever inserts one."""
    with pytest.raises(Denied):
        await PostgresAccounts(db.pool).resolve(claims(email="stranger@example.test"))

    assert await live_db.fetchval(
        "select count(*) from config.accounts where email = 'stranger@example.test'"
    ) == 0


async def test_the_first_verified_sign_in_binds_the_auth_user(live_db):
    """`sub` is the one column the application writes: the seed is written
    before the auth user exists, and this is where the two meet."""
    await PostgresAccounts(db.pool).resolve(claims(sub="auth-user-1"))

    assert await live_db.fetchval(
        "select sub from config.accounts where email = $1", ALICE
    ) == "auth-user-1"


async def test_a_second_auth_user_cannot_claim_a_bound_account(live_db):
    """Otherwise deleting the Supabase user and signing up again with the same
    address would inherit the caseload — which is exactly the self-serve path
    the seed exists to close."""
    accounts = PostgresAccounts(db.pool)
    await accounts.resolve(claims(sub="auth-user-1"))

    with pytest.raises(Denied):
        await accounts.resolve(claims(sub="somebody-else"))


async def test_binding_is_idempotent_across_sign_ins(live_db):
    accounts = PostgresAccounts(db.pool)
    await accounts.resolve(claims(sub="auth-user-1"))
    resolved = await accounts.resolve(claims(sub="auth-user-1"))

    assert resolved.sub == "auth-user-1"


# ─── the review table ────────────────────────────────────────────────────────


async def test_interviews_carries_what_the_review_table_renders(live_db):
    interview = await resolve_interview()

    rows = await reads.interviews(user())
    row = next(r for r in rows if r.id == interview.id)

    assert row.status == "running"
    assert row.patient_first_name == interview.patient.first_name
    assert row.patient_origin == "demo"
    assert row.protocol_id == "proto_warmup_v1"
    # Joined from `config.protocols`, so the table reads as a clinician wrote it
    # rather than as an id.
    assert row.protocol_label == PROTOCOLS["proto_warmup_v1"].label
    assert row.started_at is not None


async def test_a_completed_call_shows_its_outcome(live_db):
    interview = await resolve_interview()
    session = await create_session(interview, PROTOCOLS[interview.protocol_id])
    await end_session(session, "complete")
    await session.writer.close()

    rows = await reads.interviews(user())
    row = next(r for r in rows if r.id == interview.id)

    assert row.status == "abandoned"  # the machine never captured anything
    assert row.outcome == "complete"
    assert row.ended_at is not None


async def test_the_review_table_is_bounded(live_db):
    """A route with no ceiling is one `select *` away from streaming the whole
    record down a phone connection."""
    for _ in range(3):
        await resolve_interview()

    assert len(await reads.interviews(user(), limit=2)) == 2
    assert reads.clamp(10_000) == reads.MAX_LIMIT
    assert reads.clamp(None) == reads.DEFAULT_LIMIT


# ─── one interview ───────────────────────────────────────────────────────────


async def test_the_detail_carries_the_transcript_and_the_results(live_db):
    interview = await resolve_interview()
    session = await create_session(interview, PROTOCOLS[interview.protocol_id])
    session.machine.capture("day_mood", "Not bad, thanks")
    await end_session(session, "complete")
    await session.writer.close()

    detail = await reads.interview(user(), interview.id)

    assert detail.interview.id == interview.id
    assert [f.field_key for f in detail.results] == ["anything_else", "day_mood"]
    assert next(f for f in detail.results if f.field_key == "day_mood").value == "Not bad, thanks"
    # In `seq` order, which is the order the call happened in.
    assert [e.seq for e in detail.events] == sorted(e.seq for e in detail.events)
    assert [e.type for e in detail.events] == ["session.created", "session.ended"]


async def test_a_safety_scan_that_matched_nothing_still_reaches_the_screen(live_db):
    """The dashboard's detail screen is specified on the cleared scans — they are
    the only evidence the gate ran on a turn it let through. A route that
    filtered them would look identical and could not be un-dropped from the UI.
    """
    from services.agent.session_log import SafetyScanned

    interview = await resolve_interview()
    session = await create_session(interview, PROTOCOLS[interview.protocol_id])
    session.writer.append(SafetyScanned(blocked=False, hits=[]))
    session.writer.append(SafetyScanned(blocked=True, hits=["chest_pain"], action="end_call"))
    await end_session(session, "safety")
    await session.writer.close()

    detail = await reads.interview(user(), interview.id)
    scans = [e for e in detail.events if e.type == "safety.scanned"]

    assert len(scans) == 2
    assert scans[0].payload["blocked"] is False
    assert scans[0].payload["hits"] == []
    assert scans[1].payload["action"] == "end_call"


async def test_an_interview_that_does_not_exist_is_not_found(live_db):
    with pytest.raises(reads.NotFound):
        await reads.interview(user(), "iv_never_existed")


# ─── scoping ─────────────────────────────────────────────────────────────────


async def test_a_clinician_does_not_see_another_clinicians_patient(live_db):
    """The test the `clinician_email` column exists for."""
    hers = await a_patient_of(live_db, ALICE, first_name="Ada")
    his = await a_patient_of(live_db, BOB, first_name="Bruno")

    mine = {p.id for p in await reads.patients(user(ALICE))}

    assert hers in mine
    assert his not in mine


async def test_a_clinician_does_not_see_another_clinicians_interview(live_db):
    """Scoping the patients screen and leaving the interview open would be worse
    than not scoping either: the list would say one thing and the row it links
    to would say another."""
    his = await a_patient_of(live_db, BOB, first_name="Bruno")
    his_interview = await an_interview_for(live_db, his)

    assert his_interview not in {r.id for r in await reads.interviews(user(ALICE))}

    with pytest.raises(reads.NotFound):
        await reads.interview(user(ALICE), his_interview)

    # And it is genuinely there — otherwise this test passes for the wrong reason.
    assert (await reads.interview(user(BOB), his_interview)).interview.id == his_interview


async def test_the_unowned_demo_rows_are_visible_to_everyone(live_db):
    """A portfolio deployment's demo is the shared exhibit. Nobody was
    dispatched those calls, and hiding them would leave the dashboard empty."""
    interview = await resolve_interview()

    assert interview.id in {r.id for r in await reads.interviews(user(ALICE))}
    assert interview.id in {r.id for r in await reads.interviews(user(BOB))}


async def test_the_patients_screen_distinguishes_a_demo_visitor(live_db):
    interview = await resolve_interview()
    dispatched = await a_patient_of(live_db, ALICE, first_name="Ada")

    rows = {p.id: p for p in await reads.patients(user(ALICE))}

    assert rows[interview.patient.id].origin == "demo"
    assert rows[interview.patient.id].clinician_email is None
    assert rows[dispatched].origin == "dispatched"
    assert rows[dispatched].clinician_email == ALICE


async def test_a_patient_row_counts_their_interviews(live_db):
    patient = await a_patient_of(live_db, ALICE, first_name="Ada")
    await an_interview_for(live_db, patient)

    row = next(p for p in await reads.patients(user(ALICE)) if p.id == patient)

    assert row.interview_count == 1
    assert row.last_interview_at is not None


async def test_a_patient_with_no_interviews_counts_zero(live_db):
    """A left join, so someone dispatched nothing yet is still on the list —
    and counts zero rather than one null row."""
    patient = await a_patient_of(live_db, ALICE, first_name="Ines")

    row = next(p for p in await reads.patients(user(ALICE)) if p.id == patient)

    assert row.interview_count == 0
    assert row.last_interview_at is None
