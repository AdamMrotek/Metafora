"""Red flags a human clears, against a real Postgres.

The band is the one thing on the dashboard that says a person owes an answer,
so every assertion here is about a difference: a yellow is not a red, another
clinician's red is not yours, and a call still running carries its flag from the
moment the gate scanned rather than from the moment it hangs up.

The acknowledgement is the only write. It is one `update` with `coalesce`, and
the two properties that shape stands for — a second click does not reassign the
decision, and a stranger's click does nothing at all — are at the bottom.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import pytest

from services.core import acknowledgements, db, reads

pytestmark = pytest.mark.postgres

ALICE = "alice@example.test"
BOB = "bob@example.test"

#: Two flags of `PREOP_CHECK_V1`, as it ships today: one red, one yellow.
#: Named here rather than looked up so that a test failing after 5b·1 moves the
#: flag set says *which* flag moved.
RED = "rf_anticoagulant_taken"
YELLOW = "yf_attendance_risk"
PROTOCOL = "proto_preop_check_v1"

#: `PREOP_CHECK_V1.urgent.timeout_minutes`. The deadline the band draws comes
#: from the version the interview pinned, so this is the number it must produce.
TIMEOUT_MINUTES = 120


def user(email: str = ALICE):
    from shared.auth import CurrentUser

    return CurrentUser(email=email, sub="auth-user", role="clinician", display_name="Test")


@pytest.fixture
async def live_db(pool):
    """Two clinicians, because half of what this file asserts is the difference
    between them."""
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


_seq = iter(range(1, 10_000))


async def a_call(
    pool,
    *,
    owner: str | None = ALICE,
    flags: tuple[str, ...] = (),
    status: str = "completed",
    first_name: str = "Nadia",
) -> str:
    """One patient, one interview, and one safety scan carrying `flags`.

    The scan is inserted directly rather than driven through a session: what is
    being asserted is the read over `transcript.events`, and a real call would
    put the flag ids in the same column by a longer route.
    """
    n = next(_seq)
    patient_id = f"pt_esc_{n}"
    interview_id = f"iv_esc_{n}"
    await pool.execute(
        "insert into clinical.patients (id, first_name, origin, clinician_email) "
        "values ($1, $2, 'dispatched', $3)",
        patient_id,
        first_name,
        owner,
    )
    await pool.execute(
        "insert into clinical.interviews (id, protocol_id, patient_id, status, started_at) "
        "values ($1, $2, $3, $4, now())",
        interview_id,
        PROTOCOL,
        patient_id,
        status,
    )
    await pool.execute(
        "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
        "values ($1, $2, 1, 'safety.scanned', now(), $3)",
        interview_id,
        f"sess_esc_{n}",
        {"blocked": False, "hits": list(flags), "action": _worst(flags)},
    )
    return interview_id


def _worst(flags: tuple[str, ...]) -> str | None:
    """The action `safety.scan` would have recorded for this turn: the worst of
    what it matched, over the whole turn. Writing a fixture that does not obey
    that is how a test invents a divergence the gate cannot produce — the
    `urgent` tile reads this field and the band resolves each hit against the
    pinned protocol, and the two agree only because a real scan makes them."""
    from services.agent.config.protocol import PROTOCOLS
    from services.agent.safety import SEVERITY

    by_id = {f.id: f for f in PROTOCOLS[PROTOCOL].red_flags}
    actions = [by_id[f].action for f in flags if f in by_id]
    return max(actions, key=lambda a: SEVERITY[a]) if actions else None


# ─── what reaches the band ───────────────────────────────────────────────────


async def test_a_red_flag_raises_a_line_naming_what_is_owed(live_db):
    """The whole point of the read: which flag, and by when. Neither is on
    `InterviewSummary`, which is why this is not a filter over it."""
    interview_id = await a_call(live_db, flags=(RED,), first_name="Sarah")

    band = {e.interview_id: e for e in await reads.escalations(user())}

    assert interview_id in band
    line = band[interview_id]
    assert line.patient_first_name == "Sarah"
    assert line.flag_label == "Anticoagulant not stopped as instructed"
    assert line.action == "urgent_escalate"
    assert line.due_at is not None
    assert (line.due_at - line.raised_at).total_seconds() == TIMEOUT_MINUTES * 60


async def test_a_yellow_flag_raises_no_band(live_db):
    """Every call produces yellows. A band that was usually present would be a
    band nobody reads."""
    interview_id = await a_call(live_db, flags=(YELLOW,))

    assert interview_id not in {e.interview_id for e in await reads.escalations(user())}


async def test_a_red_on_a_running_call_is_in_the_band(live_db):
    """The clock starts at the scan, not the hangup. A triage flag exists the
    moment the gate scans and the call then runs on for minutes."""
    interview_id = await a_call(live_db, flags=(RED,), status="running")

    assert interview_id in {e.interview_id for e in await reads.escalations(user())}


async def test_the_band_names_the_red_and_not_the_yellow_beside_it(live_db):
    """One line per interview, and it is the worst flag on the call. The scan's
    payload carries one action for the whole turn, so a read that trusted it
    could name the yellow and call it urgent."""
    interview_id = await a_call(live_db, flags=(YELLOW, RED))

    line = next(e for e in await reads.escalations(user()) if e.interview_id == interview_id)

    assert line.flag_label == "Anticoagulant not stopped as instructed"


async def test_another_clinicians_red_is_not_in_your_band(live_db):
    """`OWNED_BY`, verbatim, in a fourth query."""
    his = await a_call(live_db, owner=BOB, flags=(RED,))

    assert his not in {e.interview_id for e in await reads.escalations(user(ALICE))}
    # And it is genuinely there, or this passes for the wrong reason.
    assert his in {e.interview_id for e in await reads.escalations(user(BOB))}


async def test_the_urgent_tile_counts_what_the_band_draws(live_db):
    """The band's own sentence quotes the tile's number, so the two coming from
    different predicates is invariant 4's failure in a new dress."""
    await a_call(live_db, flags=(RED,))
    await a_call(live_db, flags=(YELLOW,))

    counted = await reads.overview(user())

    assert counted.urgent == len(counted.escalations)
    assert counted.urgent >= 1


# ─── the acknowledgement ─────────────────────────────────────────────────────


async def test_acknowledging_clears_the_band_and_the_tile(live_db):
    interview_id = await a_call(live_db, flags=(RED,))
    before = await reads.overview(user())

    await acknowledgements.acknowledge(user(), interview_id)

    after = await reads.overview(user())
    assert interview_id not in {e.interview_id for e in after.escalations}
    assert after.urgent == before.urgent - 1


async def test_an_acknowledged_red_is_still_a_flagged_call(live_db):
    """Taking a flag does not unflag the call. It moves from "somebody has to"
    to "somebody has", which is the `flagged` tile."""
    interview_id = await a_call(live_db, flags=(RED,))
    before = await reads.overview(user())

    await acknowledgements.acknowledge(user(), interview_id)

    after = await reads.overview(user())
    assert after.flagged == before.flagged + 1


async def test_a_second_acknowledgement_does_not_reassign_it(live_db):
    """Idempotent and not reversible. A second click returning the first stamp
    is what stops the record quietly saying whoever clicked last owns it.

    On an unowned call, because that is the only one two clinicians can both
    reach — the demo roster is the shared exhibit, and it is exactly where two
    people can race for the same flag."""
    interview_id = await a_call(live_db, owner=None, flags=(RED,))

    first = await acknowledgements.acknowledge(user(ALICE), interview_id)
    second = await acknowledgements.acknowledge(user(BOB), interview_id)

    assert second.acknowledged_by == ALICE == first.acknowledged_by
    assert second.acknowledged_at == first.acknowledged_at


async def test_a_stranger_cannot_acknowledge_your_escalation(live_db):
    """Scoped inside the `update`, not in front of it — and answered as a read
    outside scope is, because a 403 would confirm the id names a real call."""
    his = await a_call(live_db, owner=BOB, flags=(RED,))

    with pytest.raises(reads.NotFound):
        await acknowledgements.acknowledge(user(ALICE), his)

    assert his in {e.interview_id for e in await reads.escalations(user(BOB))}


async def test_acknowledging_an_interview_that_does_not_exist_is_not_found(live_db):
    with pytest.raises(reads.NotFound):
        await acknowledgements.acknowledge(user(), "iv_never_existed")
