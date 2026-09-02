"""Queueing a call, against a real Postgres.

The reason this file exists rather than folding into `test_reads.py`:
`dispatch.py` is the first thing in the repo that writes
`clinical.patients.clinician_email`, so it is the first thing that can make
`reads.OWNED_BY` mean something. The scoping assertions at the bottom mirror
`tests/test_reads.py` deliberately — a dispatched row has to be invisible to
another clinician for exactly the same reason a seeded one is.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

from datetime import UTC, datetime, timedelta

import pytest

from services.core import db, dispatch, reads
from shared.auth import CurrentUser
from shared.contracts.models import DispatchRequest

pytestmark = pytest.mark.postgres

ALICE = "alice@example.test"
BOB = "bob@example.test"


def user(email: str = ALICE) -> CurrentUser:
    return CurrentUser(email=email, sub="auth-user", role="clinician", display_name="Test")


@pytest.fixture
async def live_db(pool):
    """Two clinicians, because every scoping assertion here is about the
    difference between them."""
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


def to(first_name: str = "Nadia", **kwargs) -> DispatchRequest:
    return DispatchRequest(
        first_name=first_name, protocol_id=kwargs.pop("protocol_id", "proto_warmup_v1"), **kwargs
    )


# ─── what a dispatch writes ──────────────────────────────────────────────────


async def test_a_dispatched_call_lands_queued_and_owned(live_db):
    """The three facts that make a caseload: the row is waiting, it was
    dispatched rather than walked in, and it belongs to whoever sent it."""
    summary = await dispatch.create_interview(user(), to())

    row = await live_db.fetchrow(
        "select i.status, i.scheduled_for, p.origin, p.clinician_email, p.first_name "
        "from clinical.interviews i join clinical.patients p on p.id = i.patient_id "
        "where i.id = $1",
        summary.id,
    )
    assert row["status"] == "queued"
    assert row["origin"] == "dispatched"
    assert row["clinician_email"] == ALICE
    assert row["first_name"] == "Nadia"


async def test_the_summary_is_the_row_the_table_draws(live_db):
    """Read back through `reads.summary` rather than assembled here, so the row
    the composer shows on success is the row that survives a reload."""
    summary = await dispatch.create_interview(user(), to())

    assert summary == await reads.summary(user(), summary.id)
    assert summary.status == "queued"
    assert summary.protocol_label
    # A queued call has captured nothing and the denominator is still the truth
    # about the script, not about how many result rows happen to exist.
    assert (summary.captured_fields, summary.flag_count) == (0, 0)
    assert summary.total_fields > 0


async def test_a_slot_is_recorded_when_one_is_given(live_db):
    when = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)

    summary = await dispatch.create_interview(user(), to(scheduled_for=when))

    assert summary.scheduled_for == when


async def test_a_dispatched_patient_has_no_invented_identity(live_db):
    """A first name and nothing else. This product collects no demographics, and
    a seeded NHS number on a person a clinician just named would be exactly the
    invention `docs/system-map.md` forbids."""
    summary = await dispatch.create_interview(user(), to())

    assert (summary.patient_nhs_number, summary.patient_date_of_birth) == (None, None)


async def test_dispatching_to_an_existing_patient_reuses_the_row(live_db):
    first = await dispatch.create_interview(user(), to("Priya"))

    second = await dispatch.create_interview(
        user(), DispatchRequest(patient_id=first.patient_id, protocol_id="proto_warmup_v1")
    )

    assert second.patient_id == first.patient_id
    assert second.id != first.id


async def test_dispatching_to_a_demo_patient_does_not_claim_them(live_db):
    """The roster is the shared exhibit. Sending one of the ten a call must not
    take them off every other clinician's dashboard — being called by somebody
    does not make a person theirs retrospectively."""
    await live_db.execute(
        "insert into clinical.patients (id, first_name, origin) "
        "values ('pt_shared_demo', 'Iris', 'demo') on conflict (id) do nothing"
    )

    await dispatch.create_interview(
        user(), DispatchRequest(patient_id="pt_shared_demo", protocol_id="proto_warmup_v1")
    )

    assert await live_db.fetchval(
        "select clinician_email from clinical.patients where id = 'pt_shared_demo'"
    ) is None


# ─── what a dispatch refuses ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "request_",
    [
        DispatchRequest(protocol_id="proto_warmup_v1"),
        DispatchRequest(first_name="Nadia", patient_id="pt_1", protocol_id="proto_warmup_v1"),
        DispatchRequest(first_name="   ", protocol_id="proto_warmup_v1"),
        DispatchRequest(first_name="Patient 4", protocol_id="proto_warmup_v1"),
        DispatchRequest(first_name="Nadia", protocol_id="proto_does_not_exist"),
    ],
    ids=["nobody", "both", "blank", "not-a-name", "unknown-protocol"],
)
async def test_a_request_that_cannot_be_honoured_says_why(live_db, request_):
    with pytest.raises(dispatch.Refused):
        await dispatch.create_interview(user(), request_)


async def test_a_superseded_protocol_cannot_be_dispatched(live_db):
    """It is still in the catalog, because the interviews already pinned to it
    have to run and read. That is not the same as being offerable: a new call
    goes against the current version, and `OFFERED` is the difference.

    Skipped while the two sets agree — the v1 generation was deleted rather than
    superseded, so there is nothing to dispatch. The test stays because the next
    published version puts a member back in the gap, and that is exactly when
    this has to still be true."""
    from services.agent.config.protocol import OFFERED, PROTOCOLS

    superseded = next((p for p in PROTOCOLS if p not in OFFERED), None)
    if superseded is None:
        pytest.skip("every published protocol is currently offered")

    with pytest.raises(dispatch.Refused):
        await dispatch.create_interview(user(), to(protocol_id=superseded))


async def test_nothing_is_written_when_a_dispatch_is_refused(live_db):
    before = await live_db.fetchval("select count(*) from clinical.interviews")

    with pytest.raises(dispatch.Refused):
        await dispatch.create_interview(user(), to(protocol_id="proto_does_not_exist"))

    assert await live_db.fetchval("select count(*) from clinical.interviews") == before


# ─── scope ───────────────────────────────────────────────────────────────────


async def test_a_dispatched_row_appears_for_its_owner(live_db):
    """On the overview's queued list, which is what the scheduled card and the
    Deployments screen draw. Not in the review table: a call nobody has taken
    yet has nothing to review."""
    summary = await dispatch.create_interview(user(ALICE), to("Omar"))

    assert summary.id in {row.id for row in (await reads.overview(user(ALICE))).queued}
    assert summary.id not in {row.id for row in (await reads.interviews(user(ALICE))).rows}


async def test_a_dispatched_row_is_invisible_to_another_clinician(live_db):
    """The assertion the whole scope predicate exists for, and the first one in
    the repo that could fail: before dispatch, nothing set an owner."""
    summary = await dispatch.create_interview(user(ALICE), to("Omar"))

    assert summary.id not in {row.id for row in (await reads.overview(user(BOB))).queued}
    with pytest.raises(reads.NotFound):
        await reads.summary(user(BOB), summary.id)


async def test_a_clinician_cannot_dispatch_to_someone_elses_patient(live_db):
    """`NotFound` rather than a refusal that names the row: telling a caller the
    id exists is most of what they wanted to know."""
    theirs = await dispatch.create_interview(user(BOB), to("Vera"))

    with pytest.raises(reads.NotFound):
        await dispatch.create_interview(
            user(ALICE),
            DispatchRequest(patient_id=theirs.patient_id, protocol_id="proto_warmup_v1"),
        )
