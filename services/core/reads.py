"""What a clinician may read, as SQL.

Every function here takes a `CurrentUser`. That is not decoration: the door
(`shared/auth/deps.py`) decides *whether* you may read, and these decide *what*
— and the two have to be different decisions, because a system that checks a
role at the door and then runs an unscoped query has no place to put the second
one when it is needed. Today the scope is one predicate (`OWNED_BY`); when it
becomes a relationship it becomes a longer predicate in the same place.

Reads only. The writers are named and there are four of them: `store.py` closes
an interview and records what it captured, `queue.py` claims one, `dispatch.py`
creates one, and `invitations.py` mints and spends its link. Nothing in this
file writes, and the dashboard reaches the last two through their own routes
rather than through a query here.
"""

from typing import Any

from services.agent.safety import SEVERITY
from services.core import db
from shared.auth import CurrentUser
from shared.contracts.models import (
    ExperienceDay,
    ExperienceRange,
    ExperienceSummary,
    InterviewDetail,
    InterviewPage,
    InterviewSort,
    InterviewSummary,
    Overview,
    PatientSummary,
    ProtocolOption,
    ResultField,
    TranscriptEvent,
)

#: The scope, in one place. A clinician sees their own patients and the unowned
#: demo rows — the demo is the shared exhibit on a portfolio deployment, and
#: hiding it from the only account that exists would leave the dashboard empty.
#: Phase 5's dispatch is the only thing that sets an owner.
OWNED_BY = "(p.clinician_email is null or p.clinician_email = $1)"

#: A review table is read a page at a time, and a route with no ceiling is one
#: `select *` away from streaming the whole record over a phone connection.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: How many rows the escalation band and the scheduled card may draw. Both are
#: lists a person reads rather than pages through, so the ceiling is low and the
#: count beside them (`Overview.urgent`) is what says whether it was reached.
RAIL_LIMIT = 20
QUEUED_LIMIT = 50

#: When something happened, in one place. The browser used to answer this
#: (`format.ts`'s `activityAt`) and the server used to answer it differently —
#: `coalesce(started_at, created_at)` here against `ended_at ?? started_at ??
#: scheduled_for ?? created_at` there — so the order a page arrived in was not
#: the order it was drawn in, and paging over the difference is how a row lands
#: on a page nobody expected. One expression now, and the browser re-sorts
#: nothing.
_ACTIVITY = "coalesce(i.ended_at, i.started_at, i.scheduled_for, i.created_at)"

#: Triage order, and it is a clinical ordering rather than a display preference:
#: an escalation from yesterday outranks a completed call from ten minutes ago,
#: because the table exists to be worked through rather than browsed. Lifted
#: verbatim from what `screens/Dashboard.tsx` used to compute in the browser —
#: moved, not reinterpreted.
_URGENCY = """
    case when i.outcome = 'safety'    then 0
         when i.status  = 'abandoned' then 1
         else 2 end
"""

#: What the two sort options mean, as SQL. `i.id` breaks every tie, because a
#: paged query with an unstable order can show one row twice and another never.
_ORDER: dict[str, str] = {
    "urgency": f"{_URGENCY} asc, {_ACTIVITY} desc, i.id desc",
    "recent": f"{_ACTIVITY} desc, i.id desc",
}

def _search(n: int) -> str:
    """The review table's search box, as a predicate on parameter `n`.

    Three columns, because those are the three things written on the row a
    clinician is looking for. `ilike` rather than a text-search index: the list
    is a caseload, not a corpus.

    Written as a function rather than a constant because two statements need it
    at different positions — the page query takes a limit and an offset that the
    count query has no use for — and hardcoding `$3` in both is how one of them
    ends up passing a parameter nothing references.
    """
    return f"""
    (${n}::text is null or (
        p.first_name ilike '%' || ${n} || '%'
     or pr.label     ilike '%' || ${n} || '%'
     or i.id         ilike '%' || ${n} || '%'
    ))
    """


#: The review table is the *review* path, so it holds calls that happened.
#:
#: A queued interview cannot be reviewed — there is nothing to read — and it is
#: already drawn twice: on the dashboard's scheduled card and on the Deployments
#: screen's upcoming table, which is where a call still out belongs. It was in
#: here only because this query returned everything in scope and the table drew
#: what it was given; `docs/ux/clinical-dashboard.html:871` calls the panel
#: "Review ready" and every row in its table is finished. The rest of the screen
#: already agreed — none of the three tiles ever counted a queued row.
#:
#: `running` stays. A call on the line becomes review work in minutes, and it is
#: the one live thing on the screen.
_REVIEWABLE = "i.status <> 'queued'"


def _protocol(n: int) -> str:
    """Filter by the protocol's id rather than its label. The browser filtered
    on the label because the label was all it had; an id cannot collide and does
    not change when someone rewords a protocol."""
    return f"(${n}::text is null or i.protocol_id = ${n})"

_SUMMARY_COLUMNS = """
    i.id,
    i.status,
    i.outcome,
    i.patient_id,
    p.first_name  as patient_first_name,
    p.nhs_number  as patient_nhs_number,
    p.date_of_birth as patient_date_of_birth,
    p.origin      as patient_origin,
    i.protocol_id,
    pr.label      as protocol_label,
    f.captured    as captured_fields,
    d.total       as total_fields,
    g.flag_count,
    g.worst_flag,
    i.scheduled_for,
    i.started_at,
    i.ended_at,
    i.created_at
"""

#: The review table draws a "9/16 captured" meter on every row. Counting it
#: here is two lateral joins; counting it in the browser is a detail request per
#: row.
#:
#: The two halves of that meter come from different places on purpose. What was
#: captured is a fact about the call, and only `clinical.results` knows it. How
#: much there was to capture is a fact about the *protocol*, and the interview
#: pins one — so the denominator is read from the version it pinned, not from
#: how many result rows happen to exist. `clinical.results` is written when a
#: call ends, so counting it on both sides meters a queued or running interview
#: as 0/0: a meter that says the script is empty, when what it means is that
#: nothing has been written down yet. 0 of 6 is the true statement.

#: `safety.py`'s ranking, as a SQL ladder, generated from the dict itself so
#: the two cannot drift. Which flag is "worst" is a clinical ordering, and it is
#: written down in exactly one place.
_BY_SEVERITY = "case e.payload ->> 'action' " + " ".join(
    f"when '{action}' then {rank}" for action, rank in SEVERITY.items()
) + " else -1 end"

#: What the gate found, per interview. `transcript.events` holds a
#: `safety.scanned` for every committed turn — including the ones that matched
#: nothing, which are the evidence the gate ran — so the join unnests `hits` and
#: counts what came out: a scan with an empty array contributes no rows, and a
#: call the gate cleared end to end comes back 0 and null.
#:
#: Distinct rules rather than turns, because a patient who mentions their
#: anticoagulant three times has raised one flag, not three.
_SUMMARY_FROM = f"""
    from clinical.interviews i
    join clinical.patients  p  on p.id = i.patient_id
    join config.protocols   pr on pr.id = i.protocol_id
    left join lateral (
        select (count(*) filter (where r.status = 'captured'))::int as captured
        from clinical.results r where r.interview_id = i.id
    ) f on true
    left join lateral (
        select count(*)::int as total
        from jsonb_array_elements(pr.version -> 'script' -> 'sections') section,
             jsonb_array_elements(section -> 'questions')               question
    ) d on true
    left join lateral (
        select count(distinct hit)::int                                  as flag_count,
               (array_agg(e.payload ->> 'action' order by {_BY_SEVERITY} desc))[1]
                                                                        as worst_flag
        from transcript.events e,
             jsonb_array_elements_text(e.payload -> 'hits') hit
        where e.interview_id = i.id and e.type = 'safety.scanned'
    ) g on true
"""


class NotFound(LookupError):
    """No such row — or none this caller may see, which is deliberately the
    same answer. Distinguishing them tells an unauthorised reader that the id
    exists, which is most of what they wanted to know."""


def _pool() -> Any:
    pool = db.pool()
    if pool is None:
        # Only reachable with SUPABASE_URL set and DATABASE_URL not, which
        # `config.py` refuses outside dev. Loud rather than a confusing empty list.
        raise RuntimeError("no database configured — there is nothing to read")
    return pool


def clamp(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


async def interviews(
    user: CurrentUser,
    *,
    sort: InterviewSort = "urgency",
    search: str | None = None,
    protocol_id: str | None = None,
    page: int = 0,
    limit: int | None = None,
) -> InterviewPage:
    """One page of the review table, ordered and filtered here rather than
    in the browser.

    It used to be the other way round: this returned the hundred most recent
    rows and `screens/Dashboard.tsx` sorted, searched and paged them. That is
    fine at twenty interviews and wrong at a hundred and one — the browser would
    be triaging a window it could not see past, so an escalation older than the
    hundredth row would never reach the urgent tile and nothing on the screen
    would say a row had been left out. A count the caller can compare against
    the page they were given is the difference.

    Queued interviews are not here — see `_REVIEWABLE`. They are on the
    scheduled card and the Deployments screen, which is where a call still out
    belongs, and `overview.queued` is what serves both.

    `total` is counted in the same statement as the rows, so the pager cannot
    disagree with the table it is under — except on a page with no rows on it,
    where there is nothing to disagree with and the count is asked for
    separately rather than reported as zero.
    """
    size = clamp(limit)
    rows = await _pool().fetch(
        f"select {_SUMMARY_COLUMNS}, count(*) over () as total "
        f"{_SUMMARY_FROM} "
        f"where {OWNED_BY} and {_REVIEWABLE} and {_search(3)} and {_protocol(4)} "
        f"order by {_ORDER[sort]} limit $2 offset $5",
        user.email,
        size,
        _needle(search),
        protocol_id or None,
        max(page, 0) * size,
    )
    if rows:
        total = rows[0]["total"]
    else:
        # `count(*) over ()` rides on the rows, so an empty page carries no
        # total — and empty does not mean none: a reader who was on page 4 when
        # a filter narrowed the result gets no rows and still has to be told
        # there are 12, or the pager sends them nowhere and says nothing. One
        # extra statement, only on the page that has nothing to disagree with.
        total = await _pool().fetchval(
            f"select count(*) {_SUMMARY_FROM} "
            f"where {OWNED_BY} and {_REVIEWABLE} and {_search(2)} and {_protocol(3)}",
            user.email,
            _needle(search),
            protocol_id or None,
        )
    return InterviewPage(
        rows=[InterviewSummary.model_validate(dict(r)) for r in rows],
        total=total,
        page=max(page, 0),
        pages=max(1, -(-total // size)),
    )


def _needle(search: str | None) -> str | None:
    """A blank search is not a search. A bare `%` would match everything and
    cost a scan to say so, and `None` is what `_SEARCH` tests for."""
    cleaned = (search or "").strip()
    if not cleaned:
        return None
    # `%` and `_` are wildcards in `like`, and a clinician typing one means the
    # character. Escaping them here keeps the box a search box.
    return cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: Which rows are a finished call the gate did not stop. Both counted tiles are
#: about work that came back, so this is the half of the record they share.
_CAME_BACK = "i.status in ('completed', 'abandoned') and i.outcome is distinct from 'safety'"

#: The three tiles, as one row. They are three errands and not a census: what
#: the gate stopped, what it flagged on a call that ran on, and what neither
#: happened to but that stopped short of the script. A finished call falls in
#: exactly one of them, and flags outrank a short script because a flagged call
#: is read either way.
_COUNTS = f"""
    select
        count(*) filter (where i.outcome = 'safety')::int as urgent,
        count(*) filter (where {_CAME_BACK} and g.flag_count > 0)::int as flagged,
        count(*) filter (
            where {_CAME_BACK} and g.flag_count = 0 and f.captured < d.total
        )::int as incomplete
    {_SUMMARY_FROM}
    where {OWNED_BY}
"""


async def overview(user: CurrentUser) -> Overview:
    """Everything on the dashboard that is not one page of the table.

    It exists because the table stopped being the whole list. The three tiles,
    the escalation band, the scheduled card and the protocol filter were all
    computed in the browser from the same hundred-row fetch the table used — so
    the moment that fetch became one page of eight, every one of them would have
    been counting a page and calling it the record. Counted here instead, over
    the caller's whole scope, which is the only place the number is true.

    Four statements rather than one. They share `_SUMMARY_FROM` and could be
    folded into a single CTE returning four aggregates, and that query would be
    the kind nobody can read six months later; the two lists are bounded and the
    counts are one scan, so the cost of keeping them apart is a round trip on an
    open pool.
    """
    pool = _pool()

    counts = await pool.fetchrow(_COUNTS, user.email)

    # Every open escalation, newest first — the band draws the latest and says
    # how many there are, and `urgent` above is the number it trusts for that.
    escalations = await pool.fetch(
        f"select {_SUMMARY_COLUMNS} {_SUMMARY_FROM} "
        f"where {OWNED_BY} and i.outcome = 'safety' "
        f"order by {_ACTIVITY} desc, i.id desc limit $2",
        user.email,
        RAIL_LIMIT,
    )

    # What is still out, soonest first: the dashboard's scheduled card and the
    # Deployments screen's upcoming table are the same rows asked twice.
    queued = await pool.fetch(
        f"select {_SUMMARY_COLUMNS} {_SUMMARY_FROM} "
        f"where {OWNED_BY} and i.status = 'queued' "
        "order by coalesce(i.scheduled_for, i.created_at) asc, i.id asc limit $2",
        user.email,
        QUEUED_LIMIT,
    )

    # What the filter may offer: the protocols actually present in this
    # caller's record, not every protocol that exists. A filter listing an
    # option that selects nothing is a filter that lies about the record.
    protocols = await pool.fetch(
        "select distinct pr.id, pr.label "
        "from clinical.interviews i "
        "join clinical.patients p on p.id = i.patient_id "
        "join config.protocols pr on pr.id = i.protocol_id "
        f"where {OWNED_BY} order by pr.label",
        user.email,
    )

    return Overview(
        urgent=counts["urgent"],
        flagged=counts["flagged"],
        incomplete=counts["incomplete"],
        escalations=[InterviewSummary.model_validate(dict(r)) for r in escalations],
        queued=[InterviewSummary.model_validate(dict(r)) for r in queued],
        protocols=[ProtocolOption.model_validate(dict(r)) for r in protocols],
    )


async def history(user: CurrentUser, patient_id: str) -> list[InterviewSummary]:
    """Every interview for one patient, oldest first — the detail screen's
    timeline.

    Scoped like everything else here. The patient id arrives from a row the
    caller was already shown, but a route that trusted that would be one
    guessed id away from reading somebody else's history.
    """
    rows = await _pool().fetch(
        f"select {_SUMMARY_COLUMNS} {_SUMMARY_FROM} "
        f"where {OWNED_BY} and i.patient_id = $2 "
        f"order by {_ACTIVITY} asc, i.id asc limit $3",
        user.email,
        patient_id,
        MAX_LIMIT,
    )
    return [InterviewSummary.model_validate(dict(r)) for r in rows]


async def summary(user: CurrentUser, interview_id: str) -> InterviewSummary:
    """One row of the review table, on its own.

    Split out of `interview()` so that `dispatch.py` can answer with the shape
    the table already draws without assembling a second one — the row a
    clinician sees the instant they queue a call has to be the same row they see
    after a reload, and one query is how that stays true.
    """
    row = await _pool().fetchrow(
        f"select {_SUMMARY_COLUMNS} {_SUMMARY_FROM} where {OWNED_BY} and i.id = $2",
        user.email,
        interview_id,
    )
    if row is None:
        raise NotFound(interview_id)
    return InterviewSummary.model_validate(dict(row))


async def interview(user: CurrentUser, interview_id: str) -> InterviewDetail:
    """One interview, its captured fields, and its transcript.

    The transcript is unfiltered on purpose. `safety.scanned` rows that matched
    nothing are the evidence that the gate ran on a turn it cleared, and the
    dashboard's detail screen is specified on them — a route that dropped them
    could not be un-dropped from the UI.
    """
    row = await summary(user, interview_id)
    pool = _pool()

    results = await pool.fetch(
        "select field_key, label, value, status, updated_at "
        "from clinical.results where interview_id = $1 order by field_key",
        interview_id,
    )
    events = await pool.fetch(
        "select seq, type, at, payload from transcript.events "
        "where interview_id = $1 order by seq",
        interview_id,
    )
    return InterviewDetail(
        interview=row,
        results=[ResultField.model_validate(dict(r)) for r in results],
        events=[TranscriptEvent.model_validate(dict(e)) for e in events],
        # The timeline on this screen, fetched with the thing it is a timeline
        # of. It used to be filtered out of the dashboard's shared interview
        # list, which meant opening an interview directly — a bookmark, a link
        # in a message — drew no history until the list happened to arrive.
        history=await history(user, row.patient_id),
    )


#: Everything the patients table draws about one person's calls: their last
#: finished one, the next one still out, and what is still owed on them.
#:
#: Four lateral joins and no `group by`, which is the shape worth keeping — the
#: aggregate version needed every lateral column in the grouping clause, and a
#: `group by` listing seven columns is a query nobody edits twice.
#:
#: All four used to be computed in the browser by filtering the dashboard's
#: shared interview list. That stopped being possible when the list became one
#: page of the review table, and it was already wrong before: a "2 open" pill
#: counted from a hundred-row window is a number that changes when you page.
_PATIENT_ACTIVITY = """
    left join lateral (
        select count(*)::int as n from clinical.interviews i where i.patient_id = p.id
    ) all_of on true
    left join lateral (
        -- Every interview, queued ones included. `Patients.tsx` took the last
        -- entry of a history sorted over all of them, so a patient whose only
        -- call is still out has a "last interview" and always did. Moving the
        -- computation is not the moment to change what the column means.
        select i.id, i.protocol_id, pr.label as protocol_label,
               coalesce(i.ended_at, i.started_at, i.scheduled_for, i.created_at) as at
        from clinical.interviews i
        join config.protocols pr on pr.id = i.protocol_id
        where i.patient_id = p.id
        order by coalesce(i.ended_at, i.started_at, i.scheduled_for, i.created_at) desc,
                 i.id desc
        limit 1
    ) last on true
    left join lateral (
        select i.id, i.scheduled_for
        from clinical.interviews i
        where i.patient_id = p.id and i.status = 'queued'
        order by coalesce(i.scheduled_for, i.created_at) asc, i.id asc
        limit 1
    ) next on true
    left join lateral (
        select count(*)::int                   as open_count,
               bool_or(i.outcome = 'safety')    as has_escalation
        from clinical.interviews i
        where i.patient_id = p.id
          and (i.outcome = 'safety' or i.status = 'abandoned')
    ) owed on true
"""


async def patients(user: CurrentUser, *, limit: int | None = None) -> list[PatientSummary]:
    """The caller's own list, plus the unowned demo rows, carrying `origin` so
    the two are distinguishable on the screen — and carrying what the row draws
    about their last call, their next one and what is still owed on them.

    Ordered by when something last happened to them rather than when the row was
    created, because that is the column the table sorts on and a list a
    clinician scans top-down should not need the browser to reorder it.
    """
    rows = await _pool().fetch(
        "select p.id, p.first_name, p.nhs_number, p.date_of_birth, "
        "       p.origin, p.clinician_email, p.created_at, "
        "       all_of.n            as interview_count, "
        "       last.at             as last_interview_at, "
        "       last.id             as last_interview_id, "
        "       last.protocol_id    as last_protocol_id, "
        "       last.protocol_label as last_protocol_label, "
        "       next.scheduled_for  as next_scheduled_for, "
        "       coalesce(owed.open_count, 0)         as open_count, "
        "       coalesce(owed.has_escalation, false) as has_escalation "
        "from clinical.patients p "
        f"{_PATIENT_ACTIVITY}"
        f"where {OWNED_BY} "
        "order by last.at desc nulls last, p.created_at desc, p.id desc limit $2",
        user.email,
        clamp(limit),
    )
    return [PatientSummary.model_validate(dict(r)) for r in rows]


#: How many days each range draws. The labels are the spec's
#: (`docs/ux/clinical-dashboard.html`), and "all" means the fortnight the seed
#: covers rather than all of time — there is no older data to mean anything else.
_RANGE_DAYS: dict[ExperienceRange, int] = {"today": 1, "week": 7, "all": 14}

#: Every response the caller may see, scoped by the same predicate as everything
#: else here. A sentiment is not a clinical fact, but it is still keyed to a
#: patient, and a read that skipped `OWNED_BY` because the payload looked
#: harmless is exactly the read that is wrong later.
_VISIBLE_RESPONSES = f"""
    select x.sentiment, x.responded_at
    from metrics.experience_responses x
    join clinical.patients p on p.id = x.patient_id
    where {OWNED_BY}
"""


def _stamp(day: Any) -> str:
    return f"{day.day} {day:%b}"


async def experience(user: CurrentUser, range_: ExperienceRange) -> ExperienceSummary:
    """The patient-experience panel.

    The window ends at the newest response rather than at `now()`. Nothing
    writes this table — the rows are a seed — so anchoring on the clock would
    draw an empty chart the moment the deployment outlived its own migration,
    and an empty chart is not more honest than a dated one. What keeps it honest
    is `scope`, which names the fortnight instead of calling it today.
    """
    size = _RANGE_DAYS[range_]
    rows = await _pool().fetch(
        f"""
        with visible as ({_VISIBLE_RESPONSES}),
             anchor as (select max(responded_at)::date as day from visible),
             span as (
                 select generate_series(a.day - ($2::int - 1), a.day, interval '1 day')::date as day
                 from anchor a where a.day is not null
             )
        select s.day,
               count(v.sentiment) filter (where v.sentiment = 'positive')::int as positive,
               count(v.sentiment) filter (where v.sentiment = 'neutral')::int  as neutral,
               count(v.sentiment) filter (where v.sentiment = 'negative')::int as negative
        from span s
        left join visible v on v.responded_at::date = s.day
        group by s.day
        order by s.day
        """,
        user.email,
        size,
    )

    days = [
        ExperienceDay(
            # Fourteen weekday names in a row are unreadable, so the fortnight
            # is labelled by date — the same rule the browser used to apply.
            label=str(r["day"].day) if size > 7 else f"{r['day']:%a}",
            positive=r["positive"],
            neutral=r["neutral"],
            negative=r["negative"],
        )
        for r in rows
    ]
    if not days:
        return ExperienceSummary(days=[], scope="no responses yet")
    if size == 1:
        return ExperienceSummary(days=days, scope=f"responses on {_stamp(rows[0]['day'])}")
    return ExperienceSummary(
        days=days,
        scope=f"responses per day · {_stamp(rows[0]['day'])} – {_stamp(rows[-1]['day'])}",
    )
