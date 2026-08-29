"""The seeded demo record.

`services/core/seed.py` writes one interview for every state the review table
can draw, because a dashboard with three completed rows exercises one branch of
`frontend/dashboard/src/format.ts` and leaves the rest of them undrawn. What is
asserted here is that the set really is complete and really is idempotent —
it runs on every boot, so a seed that inserted twice would grow the record
without anyone noticing.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import pytest

from services.agent.config.protocol import PROTOCOLS
from services.agent.safety import SEVERITY
from services.core import db, reads, seed
from shared.auth import CurrentUser

pytestmark = pytest.mark.postgres

#: Every (status, outcome) pair `format.ts::outcome` has a sentence for. A
#: branch missing from here is a branch nobody has ever seen rendered.
EXPECTED = {
    ("completed", "complete"),
    ("abandoned", "safety"),
    ("abandoned", "patient_left"),
    ("abandoned", "max_duration"),
    ("abandoned", "server_shutdown"),
    ("abandoned", "ended_by_patient"),
    ("abandoned", "pipeline_finished"),
    ("running", None),
    ("queued", None),
}


def user(email: str = "seed@example.test") -> CurrentUser:
    return CurrentUser(email=email, sub="auth-user", role="clinician", display_name="Test")


@pytest.fixture
async def seeded(pool):
    db.configure(pool)
    await db.seed_protocols()
    await seed.seed_demo_record(pool)
    try:
        yield pool
    finally:
        db.configure(None)


async def rows(pool):
    return [r for r in await reads.interviews(user(), limit=500) if r.id.startswith("iv_demo_")]


async def test_every_state_the_review_table_can_draw_is_present(seeded):
    seen = {(r.status, r.outcome) for r in await rows(seeded)}

    assert EXPECTED <= seen, f"missing: {EXPECTED - seen}"


async def test_seeding_twice_writes_the_record_once(seeded):
    """It runs on every boot. The protocol seed has the same property and the
    same reason: `on conflict do nothing` never fires an append-only trigger."""
    before = len(await rows(seeded))
    await seed.seed_demo_record(seeded)

    assert len(await rows(seeded)) == before
    duplicated = await seeded.fetchval(
        "select count(*) from (select session_id, seq from transcript.events "
        "where session_id like 'sess_demo_%' group by 1, 2 having count(*) > 1) d"
    )
    assert duplicated == 0


async def test_a_half_finished_call_has_a_row_per_question(seeded):
    """`_record_outcome` writes the whole declared set when a call ends, so the
    meter counts captured against every question asked — not against the ones
    that happened to be answered."""
    row = next(r for r in await rows(seeded) if r.outcome == "patient_left")

    assert row.total_fields > row.captured_fields > 0

    detail = await reads.interview(user(), row.id)
    assert len(detail.results) == row.total_fields
    assert {f.status for f in detail.results} <= {"captured", "open", "pending"}
    assert sum(f.status == "open" for f in detail.results) == 1


async def test_a_running_call_has_no_results_yet(seeded):
    """`clinical.results` is written when the call ends. The Record tab says so
    rather than drawing an empty table, and it needs a row to say it on.

    The meter still knows how long the script is — that is the protocol's
    answer, not the record's — so the row reads 0 of N and not 0 of nothing.
    """
    row = next(r for r in await rows(seeded) if r.status == "running")

    declared = sum(
        len(section.questions) for section in PROTOCOLS[row.protocol_id].script.sections
    )
    assert (row.captured_fields, row.total_fields) == (0, declared)
    assert declared > 0
    assert row.ended_at is None
    assert (await reads.interview(user(), row.id)).results == []


async def test_the_stopped_call_carries_the_words_the_rail_quotes(seeded):
    """The escalation band re-reads the interview to quote the patient. That
    needs a `safety.scanned` with hits sitting after the turn it ran on."""
    row = next(r for r in await rows(seeded) if r.outcome == "safety")
    events = (await reads.interview(user(), row.id)).events

    scans = [e for e in events if e.type == "safety.scanned"]
    blocked = [e for e in scans if e.payload["blocked"]]
    assert len(blocked) == 1
    assert blocked[0].payload["action"] == "end_call"
    assert blocked[0].payload["hits"] == ["rf_self_harm"]

    # The turn it ran on is the one immediately before it, which is what
    # `transcript.ts` relies on to attach the scan to the patient's line.
    order = [e.seq for e in events]
    before = max(e for e in order if e < blocked[0].seq)
    assert next(e for e in events if e.seq == before).type == "turn.committed"


async def test_a_cleared_scan_is_recorded_on_every_answered_turn(seeded):
    """A scan that matched nothing is the evidence the gate ran. The detail
    screen draws them, so the seed has to contain them."""
    row = next(r for r in await rows(seeded) if r.outcome == "complete")
    events = (await reads.interview(user(), row.id)).events

    turns = [e for e in events if e.type == "turn.committed"]
    scans = [e for e in events if e.type == "safety.scanned"]
    assert len(turns) == len(scans)
    assert any(e.payload["hits"] == [] for e in scans)


async def test_the_row_carries_what_the_gate_found(seeded):
    """`outcome` only names the flag that *stopped* the call. An
    `urgent_escalate` or a `soft_review` lets the conversation run to the end,
    so the summary carries the count and the worst action itself.

    Asserted against the transcript rather than against a constant: the row and
    the events are two readings of one call, and the useful failure is them
    disagreeing.
    """
    for row in await rows(seeded):
        scans = [
            e.payload
            for e in (await reads.interview(user(), row.id)).events
            if e.type == "safety.scanned" and e.payload["hits"]
        ]
        hits = {hit for scan in scans for hit in scan["hits"]}
        worst = max(
            (scan["action"] for scan in scans), key=lambda a: SEVERITY[a], default=None
        )

        assert (row.flag_count, row.worst_flag) == (len(hits), worst), row.id


async def test_a_finished_call_is_either_flagged_or_clean(seeded):
    """The two tiles on the dashboard. A call the gate cleared end to end is the
    one a clinician can stop thinking about, and it only exists as a number if
    the flagged ones are countable — every action has to be represented, and at
    least one call has to carry none of them."""
    finished = [r for r in await rows(seeded) if r.status in ("completed", "abandoned")]

    assert [r for r in finished if r.flag_count == 0]
    assert {r.worst_flag for r in finished} >= {
        "end_call",
        "urgent_escalate",
        "soft_review",
        "note_only",
    }
    # The one the band above the table owns, and the only one `outcome` names.
    assert next(r for r in finished if r.outcome == "safety").worst_flag == "end_call"


async def test_a_booked_call_is_still_in_the_future(seeded):
    """Re-booked on every boot: these rows have no transcript, so moving them is
    not rewriting a record, and a scheduled card full of past dates is worse
    than no card."""
    from datetime import UTC, datetime

    booked = [r for r in await rows(seeded) if r.status == "queued"]

    assert booked
    for row in booked:
        assert row.scheduled_for is not None
        assert row.scheduled_for > datetime.now(UTC)
        assert row.created_at < datetime.now(UTC)
