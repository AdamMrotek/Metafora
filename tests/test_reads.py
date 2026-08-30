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


async def a_response_for(pool, patient_id: str, *, days_ago: int = 0) -> None:
    """One experience response, which nothing in the application writes — the
    rows are a migration's seed, so a test that wants one inserts it."""
    await pool.execute(
        "insert into metrics.experience_responses (id, patient_id, sentiment, responded_at) "
        "values ($1, $2, 'positive', now() - ($3::int * interval '1 day')) "
        "on conflict (id) do nothing",
        f"xr_test_{patient_id}_{days_ago}",
        patient_id,
        days_ago,
    )


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

    rows = (await reads.interviews(user())).rows
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

    rows = (await reads.interviews(user())).rows
    row = next(r for r in rows if r.id == interview.id)

    assert row.status == "abandoned"  # the machine never captured anything
    assert row.outcome == "complete"
    assert row.ended_at is not None


async def test_the_row_carries_how_much_of_the_script_was_captured(live_db):
    """The review table draws a meter per row. Counting it in the join is what
    stops the dashboard making a detail request per line to fill it in."""
    interview = await resolve_interview()
    session = await create_session(interview, PROTOCOLS[interview.protocol_id])
    session.machine.capture("day_mood", "Not bad, thanks")
    await end_session(session, "complete")
    await session.writer.close()

    rows = (await reads.interviews(user())).rows
    row = next(r for r in rows if r.id == interview.id)

    assert (row.captured_fields, row.total_fields) == (1, 2)
    # And the same numbers on the detail, which shares `_SUMMARY_COLUMNS`.
    detail = await reads.interview(user(), interview.id)
    assert (detail.interview.captured_fields, detail.interview.total_fields) == (1, 2)


async def test_an_interview_with_no_results_yet_meters_against_the_script(live_db):
    """`clinical.results` is written when the call ends, so a call that has not
    ended has no rows at all. The denominator still has to be right: it comes
    from the protocol the interview pinned, so this is 0/2 — nothing captured
    out of two declared questions — and not 0/0, which reads as an empty script.
    """
    interview = await resolve_interview()
    declared = sum(
        len(section.questions)
        for section in PROTOCOLS[interview.protocol_id].script.sections
    )

    row = next(r for r in (await reads.interviews(user())).rows if r.id == interview.id)

    assert (row.captured_fields, row.total_fields) == (0, declared) == (0, 2)


async def test_the_review_table_is_bounded(live_db):
    """A route with no ceiling is one `select *` away from streaming the whole
    record down a phone connection."""
    for _ in range(3):
        await resolve_interview()

    assert len((await reads.interviews(user(), limit=2)).rows) == 2
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

    assert his_interview not in {r.id for r in (await reads.interviews(user(ALICE))).rows}

    with pytest.raises(reads.NotFound):
        await reads.interview(user(ALICE), his_interview)

    # And it is genuinely there — otherwise this test passes for the wrong reason.
    assert (await reads.interview(user(BOB), his_interview)).interview.id == his_interview


async def test_the_unowned_demo_rows_are_visible_to_everyone(live_db):
    """A portfolio deployment's demo is the shared exhibit. Nobody was
    dispatched those calls, and hiding them would leave the dashboard empty."""
    interview = await resolve_interview()

    assert interview.id in {r.id for r in (await reads.interviews(user(ALICE))).rows}
    assert interview.id in {r.id for r in (await reads.interviews(user(BOB))).rows}


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


# ─── patient identity ────────────────────────────────────────────────────────


async def test_the_summaries_carry_the_identity_the_dashboard_draws(live_db):
    """NHS number and date of birth come down with the row. They used to be
    invented in the browser from the patient id; the header and the review
    table read them here now, which is the whole of Phase 5·0."""
    interview = await resolve_interview()

    row = next(r for r in (await reads.interviews(user(ALICE))).rows if r.id == interview.id)
    patient = next(p for p in await reads.patients(user(ALICE)) if p.id == interview.patient.id)

    assert row.patient_nhs_number is not None
    assert row.patient_nhs_number.startswith("999")
    assert row.patient_date_of_birth is not None
    assert patient.nhs_number == row.patient_nhs_number
    assert patient.date_of_birth == row.patient_date_of_birth


async def test_a_patient_without_an_identity_reads_as_null_rather_than_missing(live_db):
    """A dispatched patient has a first name and nothing else, and the screen
    has to draw that — an em dash, not a crash."""
    patient = await a_patient_of(live_db, ALICE, first_name="Ada")

    row = next(p for p in await reads.patients(user(ALICE)) if p.id == patient)

    assert row.nhs_number is None
    assert row.date_of_birth is None


# ─── patient experience ──────────────────────────────────────────────────────
#
# The scratch database has the migrations applied, so the seeded roster and its
# fortnight of responses are present and unowned — visible to everyone, which is
# the point of them. Every assertion below is written to hold *on top of* that
# rather than to pretend it is not there.


async def test_experience_counts_the_responses_it_can_see(live_db):
    mine = await a_patient_of(live_db, ALICE, first_name="Ada")
    await a_response_for(live_db, mine)

    panel = await reads.experience(user(ALICE), "today")

    assert len(panel.days) == 1
    assert panel.days[0].positive >= 1
    assert "responses on" in panel.scope


async def test_experience_is_scoped_like_everything_else_here(live_db):
    """A sentiment is not a clinical fact, which is exactly why a read that
    skipped `OWNED_BY` because the payload looked harmless would go unnoticed."""
    his = await a_patient_of(live_db, BOB, first_name="Bruno")
    await a_response_for(live_db, his)

    before = sum(d.positive for d in (await reads.experience(user(ALICE), "all")).days)
    await a_response_for(live_db, his, days_ago=1)
    after = sum(d.positive for d in (await reads.experience(user(ALICE), "all")).days)

    assert before == after
    assert sum(d.positive for d in (await reads.experience(user(BOB), "all")).days) >= 2


async def test_the_window_ends_at_the_newest_response_and_zero_fills_behind_it(live_db):
    """Nothing writes this table, so anchoring on `now()` would draw an empty
    chart the moment the deployment outlived its own seed. Anchoring on the data
    means the days behind the newest one can be empty — and an empty day is a
    zero bar, because dropping it silently rescales the axis.

    Owned by Bob so the moved anchor stays out of Alice's tests above.
    """
    his = await a_patient_of(live_db, BOB, first_name="Bruno")
    await a_response_for(live_db, his, days_ago=-30)

    panel = await reads.experience(user(BOB), "week")

    assert len(panel.days) == 7
    assert panel.days[-1].positive >= 1
    # The six days before it are past the end of the seed and hold nothing.
    assert all(d.positive + d.neutral + d.negative == 0 for d in panel.days[:-1])
    assert " – " in panel.scope
