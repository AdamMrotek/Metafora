"""The review table as a server-side query — sort, search, filter, page.

All four used to be decisions the browser made about a hundred rows it had
already been handed. The property this file exists for is the one that made
that wrong rather than merely awkward: a page is a window onto the *whole*
scope, so a row is on the page its order puts it on, and `total` says how many
there were. A browser sorting a truncated window cannot say either.

`tests/test_reads.py` covers what a row carries and who may see it. This file
is only about which rows come back, in what order, and how many are claimed.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import pytest

from services.core import db, reads
from shared.auth import CurrentUser

pytestmark = pytest.mark.postgres

ALICE = "alice@example.test"


def user(email: str = ALICE) -> CurrentUser:
    return CurrentUser(email=email, sub="auth-user", role="clinician", display_name="Test")


@pytest.fixture
async def live_db(pool):
    db.configure(pool)
    await db.seed_protocols()
    await pool.execute(
        "insert into config.accounts (email, role, display_name) "
        "values ($1, 'clinician', 'Dr Alice') on conflict (email) do nothing",
        ALICE,
    )
    try:
        yield pool
    finally:
        db.configure(None)


async def a_row(
    pool,
    key: str,
    *,
    first_name: str = "Nadia",
    status: str = "completed",
    outcome: str | None = "complete",
    protocol_id: str = "proto_warmup_v1",
    minutes_ago: int = 0,
) -> str:
    """One interview owned by Alice, at a chosen point in the past."""
    patient_id, interview_id = f"pt_tbl_{key}", f"iv_tbl_{key}"
    await pool.execute(
        "insert into clinical.patients (id, first_name, origin, clinician_email) "
        "values ($1, $2, 'dispatched', $3) on conflict (id) do nothing",
        patient_id,
        first_name,
        ALICE,
    )
    await pool.execute(
        "insert into clinical.interviews "
        "(id, protocol_id, patient_id, status, outcome, started_at, ended_at, created_at) "
        "values ($1, $2, $3, $4, $5, "
        "        now() - ($6::int * interval '1 minute'), "
        "        case when $4 = 'queued' then null "
        "             else now() - ($6::int * interval '1 minute') end, "
        "        now() - ($6::int * interval '1 minute')) "
        "on conflict (id) do nothing",
        interview_id,
        protocol_id,
        patient_id,
        status,
        outcome,
        minutes_ago,
    )
    return interview_id


async def a_red_flag_on(
    pool,
    interview_id: str,
    *,
    hit: str = "rf_self_harm",
    action: str = "end_call",
) -> None:
    """One `safety.scanned` carrying a red, which is what the `urgent` tile
    counts since Phase 5b.

    `outcome = 'safety'` alone no longer reaches it. That column is what the
    gate *stopped*, and the tile is now "a red nobody has acknowledged" — a
    fact about `transcript.events` and one column on the interview, so a row
    that claims an outcome and logged no scan is a row nothing raised.

    `hit` has to be a flag the interview's own protocol declares, because the
    read resolves the action through the pinned version rather than trusting
    the one in the payload — an id the version does not name resolves to no
    action at all.
    """
    await pool.execute(
        "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
        "values ($1, $2, 1, 'safety.scanned', now(), $3) on conflict do nothing",
        interview_id,
        f"sess_tbl_{interview_id}",
        {"blocked": action == "end_call", "hits": [hit], "action": action},
    )


def ids(page) -> list[str]:
    return [r.id for r in page.rows]


def mine(page, made: set[str]) -> list[str]:
    """Only the rows this test made. The scratch database is shared across the
    postgres suite, so an assertion on the whole page would be an assertion
    about every other file in it."""
    return [i for i in ids(page) if i in made]


# ─── order ───────────────────────────────────────────────────────────────────


async def test_recent_puts_the_newest_call_first(live_db):
    """The complaint this whole change came from: a call you have just taken
    has to be findable without hunting for it."""
    old = await a_row(live_db, "r_old", minutes_ago=600)
    new = await a_row(live_db, "r_new", minutes_ago=1)
    made = {old, new}

    page = await reads.interviews(user(), sort="recent", limit=500)

    assert mine(page, made) == [new, old]


async def test_urgency_puts_work_owed_above_recency(live_db):
    """A clinical ordering, not a display preference: an escalation from
    yesterday outranks a completed call from ten minutes ago, because the table
    exists to be worked through rather than browsed.

    Moved from the browser unchanged — this is the assertion that says so."""
    fresh = await a_row(live_db, "u_fresh", minutes_ago=1)
    abandoned = await a_row(
        live_db, "u_left", status="abandoned", outcome="patient_left", minutes_ago=300
    )
    escalated = await a_row(
        live_db, "u_safe", status="abandoned", outcome="safety", minutes_ago=900
    )
    made = {fresh, abandoned, escalated}

    page = await reads.interviews(user(), sort="urgency", limit=500)

    assert mine(page, made) == [escalated, abandoned, fresh]


async def test_urgency_ranks_a_triage_red_by_its_flag_and_not_its_ending(live_db):
    """A red that let the call finish outranks an abandoned call.

    `urgent_escalate` does not stop the interview, so the row is
    `completed / complete` and its ending says nothing about how grave it is.
    Ranking on `i.outcome = 'safety'` alone put it below every abandoned call
    — while the escalation band, which ranks on the flag's action, was pointing
    a clinician straight at it. The band and the table it links into were
    ordering the same call by two different facts.
    """
    triage = await a_row(
        live_db, "u_triage", protocol_id="proto_preop_check_v2", minutes_ago=900
    )
    await a_red_flag_on(
        live_db, triage, hit="surgical_site_change", action="urgent_escalate"
    )
    abandoned = await a_row(
        live_db, "u_triage_left", status="abandoned", outcome="patient_left", minutes_ago=300
    )
    fresh = await a_row(live_db, "u_triage_fresh", minutes_ago=1)
    made = {triage, abandoned, fresh}

    page = await reads.interviews(user(), sort="urgency", limit=500)

    assert mine(page, made) == [triage, abandoned, fresh]


async def test_urgency_keeps_a_red_up_the_table_once_it_is_acknowledged(live_db):
    """Taking a call says somebody has it, not that it has been read.

    The band clears on acknowledgement and the `urgent` tile drops it, both
    deliberately. The sort does not: the table is where the reading happens, so
    a row that fell to the bottom the moment it was claimed would be hidden
    from the person who claimed it.
    """
    taken = await a_row(
        live_db, "u_taken", protocol_id="proto_preop_check_v2", minutes_ago=900
    )
    await a_red_flag_on(
        live_db, taken, hit="surgical_site_change", action="urgent_escalate"
    )
    await live_db.execute(
        "update clinical.interviews set acknowledged_at = now(), acknowledged_by = $2 "
        "where id = $1",
        taken,
        ALICE,
    )
    fresh = await a_row(live_db, "u_taken_fresh", minutes_ago=1)
    made = {taken, fresh}

    page = await reads.interviews(user(), sort="urgency", limit=500)

    assert mine(page, made) == [taken, fresh]


async def test_the_order_breaks_ties_so_paging_cannot_repeat_a_row(live_db):
    """Two interviews at the same instant. Without `i.id` in the order, the
    database may return them either way round per statement — which shows one
    row on both pages and the other on neither."""
    a = await a_row(live_db, "tie_a", minutes_ago=42)
    b = await a_row(live_db, "tie_b", minutes_ago=42)
    made = {a, b}

    first = mine(await reads.interviews(user(), sort="recent", limit=500), made)
    again = mine(await reads.interviews(user(), sort="recent", limit=500), made)

    assert first == again


# ─── paging ──────────────────────────────────────────────────────────────────


async def test_a_page_is_a_window_onto_the_whole_scope(live_db):
    """`total` counts what matched, not what was returned. It is the number the
    browser could never compute, and the reason the pager can be trusted."""
    for i in range(5):
        await a_row(live_db, f"pg_{i}", first_name="Pageable", minutes_ago=i)

    page = await reads.interviews(user(), search="Pageable", page=0, limit=2)

    assert len(page.rows) == 2
    assert page.total == 5
    assert page.pages == 3
    assert page.page == 0


async def test_the_pages_partition_the_result(live_db):
    """Every row exactly once across the pages — the property that fails when
    the order is unstable or the offset is computed from the wrong size."""
    made = {await a_row(live_db, f"pt_{i}", first_name="Partition", minutes_ago=i)
            for i in range(5)}

    seen: list[str] = []
    for n in range(3):
        seen += ids(await reads.interviews(user(), search="Partition", page=n, limit=2))

    assert sorted(seen) == sorted(made)


async def test_a_page_past_the_end_is_empty_rather_than_an_error(live_db):
    await a_row(live_db, "end_1", first_name="Solitary")

    page = await reads.interviews(user(), search="Solitary", page=9, limit=10)

    assert page.rows == []
    # The total still describes the query, so the browser can send the reader
    # back to a page that exists rather than showing them nothing and no reason.
    assert page.total == 1


async def test_an_empty_result_still_reads_as_page_one_of_one(live_db):
    page = await reads.interviews(user(), search="nobody-is-called-this")

    assert (page.rows, page.total, page.pages) == ([], 0, 1)


# ─── search and filter ───────────────────────────────────────────────────────


async def test_search_matches_the_three_things_written_on_the_row(live_db):
    row = await a_row(live_db, "find_me", first_name="Zephyrine")

    for needle in ("Zephyrine", "zephyr", "iv_tbl_find_me", "Warm"):
        page = await reads.interviews(user(), search=needle, limit=500)
        assert row in ids(page), f"{needle!r} found nothing"


async def test_a_blank_search_is_not_a_search(live_db):
    await a_row(live_db, "blank_1")

    assert (await reads.interviews(user(), search="   ")).total == (
        await reads.interviews(user())
    ).total


async def test_a_wildcard_typed_into_the_box_is_a_character(live_db):
    """`%` and `_` mean something to `like`. A clinician typing one means the
    character, and a search box that matched everything on `%` would look broken
    in the one way nobody reports.

    `_` is tested through a name rather than on its own: every interview id in
    this repo contains an underscore, so a bare `_` matching everything is the
    *correct* answer once it is escaped, and would prove nothing either way.
    `n_dia` is the discriminating case — a wildcard matches Nadia, a character
    does not."""
    await a_row(live_db, "pct_1", first_name="Nadia")

    assert (await reads.interviews(user(), search="%")).total == 0
    assert (await reads.interviews(user(), search="n_dia")).total == 0
    assert (await reads.interviews(user(), search="nadia")).total > 0


async def test_the_protocol_filter_selects_by_id(live_db):
    warm = await a_row(live_db, "flt_warm", protocol_id="proto_warmup_v1")
    preop = await a_row(live_db, "flt_preop", protocol_id="proto_preop_check_v2")
    made = {warm, preop}

    page = await reads.interviews(user(), protocol_id="proto_preop_check_v2", limit=500)

    assert mine(page, made) == [preop]


async def test_search_and_filter_compose(live_db):
    keep = await a_row(live_db, "cmp_1", first_name="Composable", protocol_id="proto_warmup_v1")
    drop = await a_row(
        live_db, "cmp_2", first_name="Composable", protocol_id="proto_preop_check_v2"
    )
    made = {keep, drop}

    page = await reads.interviews(
        user(), search="Composable", protocol_id="proto_warmup_v1", limit=500
    )

    assert mine(page, made) == [keep]


async def test_a_page_is_still_scoped_to_the_caller(live_db):
    """Sort, search and filter are new; the scope predicate is not, and every
    one of them has to compose with it rather than around it."""
    await live_db.execute(
        "insert into config.accounts (email, role, display_name) "
        "values ('bob@example.test', 'clinician', 'Dr Bob') on conflict (email) do nothing"
    )
    mine_id = await a_row(live_db, "scope_1", first_name="Scoped")

    page = await reads.interviews(user("bob@example.test"), search="Scoped", limit=500)

    assert mine_id not in ids(page)


# ─── the overview ────────────────────────────────────────────────────────────


async def test_the_tiles_count_the_whole_scope_not_a_page(live_db):
    """The reason `overview` exists. These three were counted in the browser
    from the same fetch the table used, so the moment that fetch became one page
    they would have been counting a page and calling it the caseload."""
    for i in range(3):
        made = await a_row(
            live_db, f"ov_{i}", status="abandoned", outcome="safety", minutes_ago=i
        )
        await a_red_flag_on(live_db, made)

    page = await reads.interviews(user(), limit=1)
    overview = await reads.overview(user())

    assert len(page.rows) == 1
    assert overview.urgent >= 3
    # And the band draws from a list that is not the page either — and states
    # the same number, because both come from `UNACKNOWLEDGED_RED`.
    assert len(overview.escalations) == overview.urgent >= 3
    assert all(e.action in reads.RED_ACTIONS for e in overview.escalations)


async def test_the_overview_carries_what_is_still_out(live_db):
    queued = await a_row(live_db, "q_1", status="queued", outcome=None)

    overview = await reads.overview(user())

    assert queued in {r.id for r in overview.queued}
    assert all(r.status == "queued" for r in overview.queued)


async def test_the_filter_offers_only_protocols_in_the_record(live_db):
    """A filter listing an option that selects nothing is a filter that lies
    about the record."""
    await a_row(live_db, "opt_1", protocol_id="proto_warmup_v1")

    overview = await reads.overview(user())
    offered = {p.id for p in overview.protocols}

    assert "proto_warmup_v1" in offered
    for option in overview.protocols:
        assert (await reads.interviews(user(), protocol_id=option.id, limit=1)).total > 0


async def test_the_escalation_band_is_bounded(live_db):
    """A band is read, not paged. `urgent` is the number it states, and the list
    behind it has a ceiling so one bad afternoon cannot stream the record."""
    overview = await reads.overview(user())

    assert len(overview.escalations) <= reads.RAIL_LIMIT
    assert len(overview.queued) <= reads.QUEUED_LIMIT


# ─── one patient's history ───────────────────────────────────────────────────


async def test_a_patients_history_comes_back_oldest_first(live_db):
    """The detail screen's timeline reads left to right in time."""
    first = await a_row(live_db, "h_1", first_name="Historic", minutes_ago=900)
    # Same patient, so reuse the row `a_row` made for `h_1`.
    patient_id = "pt_tbl_h_1"
    await live_db.execute(
        "insert into clinical.interviews (id, protocol_id, patient_id, status, outcome, "
        "started_at, ended_at, created_at) values "
        "('iv_tbl_h_2', 'proto_warmup_v1', $1, 'completed', 'complete', "
        " now() - interval '1 minute', now() - interval '1 minute', now() - interval '1 minute')"
        " on conflict (id) do nothing",
        patient_id,
    )

    history = await reads.history(user(), patient_id)

    assert [r.id for r in history] == [first, "iv_tbl_h_2"]


async def test_the_detail_carries_its_own_history(live_db):
    """Fetched with the thing it is a timeline of, so opening an interview from
    a bookmark draws the same screen as reaching it from the table."""
    interview_id = await a_row(live_db, "hd_1", first_name="Bookmarked")

    detail = await reads.interview(user(), interview_id)

    assert interview_id in {r.id for r in detail.history}


async def test_a_history_is_scoped_like_everything_else(live_db):
    """The patient id arrives from a row the caller was shown, and a route that
    treated that as permission would be one guessed id away from somebody
    else's history."""
    await live_db.execute(
        "insert into config.accounts (email, role, display_name) "
        "values ('bob@example.test', 'clinician', 'Dr Bob') on conflict (email) do nothing"
    )
    await a_row(live_db, "hs_1", first_name="Private")

    assert await reads.history(user("bob@example.test"), "pt_tbl_hs_1") == []


# ─── what the review path holds ──────────────────────────────────────────────


async def test_a_call_still_out_is_not_in_the_review_table(live_db):
    """The review table is the review path, and a queued interview cannot be
    reviewed — there is nothing to read.

    It was in here only because the query returned everything in scope and the
    table drew what it was given. `docs/ux/clinical-dashboard.html:871` names
    the panel "Review ready" and every row of its table is a finished call; the
    rest of the screen already agreed, because none of the three tiles ever
    counted a queued row.
    """
    queued = await a_row(live_db, "rv_q", status="queued", outcome=None)
    done = await a_row(live_db, "rv_d", status="completed", outcome="complete")
    made = {queued, done}

    page = await reads.interviews(user(), sort="recent", limit=500)

    assert mine(page, made) == [done]


async def test_a_call_on_the_line_is_still_in_the_table(live_db):
    """`running` stays: it becomes review work in minutes, and it is the one
    live thing on the screen."""
    live = await a_row(live_db, "rv_run", status="running", outcome=None)

    assert live in ids(await reads.interviews(user(), sort="recent", limit=500))


async def test_what_is_out_is_drawn_from_the_overview_instead(live_db):
    """Excluding it from the table does not hide it — it moves it to the two
    places a call still out belongs, both served by one list."""
    queued = await a_row(live_db, "rv_ov", status="queued", outcome=None)

    overview = await reads.overview(user())

    assert queued in {r.id for r in overview.queued}


async def test_the_total_counts_only_what_the_table_shows(live_db):
    """The pager and the greeting both read `total`. A count that included rows
    the table filters out would send a reader to a page that draws nothing."""
    await a_row(live_db, "rv_t1", first_name="Countable", status="queued", outcome=None)
    await a_row(live_db, "rv_t2", first_name="Countable", status="completed")

    page = await reads.interviews(user(), search="Countable", limit=500)

    assert page.total == len(page.rows) == 1
