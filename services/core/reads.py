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
    InterviewSummary,
    PatientSummary,
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


async def interviews(user: CurrentUser, *, limit: int | None = None) -> list[InterviewSummary]:
    """The review table. Newest activity first — which is when the call
    happened, not when the row was queued, so a backlog dispatched last week
    does not bury this morning's completed interview."""
    rows = await _pool().fetch(
        f"select {_SUMMARY_COLUMNS} {_SUMMARY_FROM} where {OWNED_BY} "
        "order by coalesce(i.started_at, i.created_at) desc, i.id desc limit $2",
        user.email,
        clamp(limit),
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
    )


async def patients(user: CurrentUser, *, limit: int | None = None) -> list[PatientSummary]:
    """The caller's own list, plus the unowned demo rows, carrying `origin` so
    the two are distinguishable on the screen."""
    rows = await _pool().fetch(
        "select p.id, p.first_name, p.nhs_number, p.date_of_birth, "
        "       p.origin, p.clinician_email, p.created_at, "
        "       count(i.id)::int as interview_count, max(i.created_at) as last_interview_at "
        "from clinical.patients p "
        "left join clinical.interviews i on i.patient_id = p.id "
        f"where {OWNED_BY} "
        "group by p.id "
        "order by p.created_at desc, p.id desc limit $2",
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
