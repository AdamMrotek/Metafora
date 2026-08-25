"""What a clinician may read, as SQL.

Every function here takes a `CurrentUser`. That is not decoration: the door
(`shared/auth/deps.py`) decides *whether* you may read, and these decide *what*
— and the two have to be different decisions, because a system that checks a
role at the door and then runs an unscoped query has no place to put the second
one when it is needed. Today the scope is one predicate (`OWNED_BY`); when it
becomes a relationship it becomes a longer predicate in the same place.

Reads only. `services/core/store.py` and `queue.py` own every write to these
tables, and the dashboard never writes.
"""

from typing import Any

from services.core import db
from shared.auth import CurrentUser
from shared.contracts.models import (
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
    p.origin      as patient_origin,
    i.protocol_id,
    pr.label      as protocol_label,
    i.scheduled_for,
    i.started_at,
    i.ended_at,
    i.created_at
"""

_SUMMARY_FROM = """
    from clinical.interviews i
    join clinical.patients  p  on p.id = i.patient_id
    join config.protocols   pr on pr.id = i.protocol_id
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


async def interview(user: CurrentUser, interview_id: str) -> InterviewDetail:
    """One interview, its captured fields, and its transcript.

    The transcript is unfiltered on purpose. `safety.scanned` rows that matched
    nothing are the evidence that the gate ran on a turn it cleared, and the
    dashboard's detail screen is specified on them — a route that dropped them
    could not be un-dropped from the UI.
    """
    pool = _pool()
    row = await pool.fetchrow(
        f"select {_SUMMARY_COLUMNS} {_SUMMARY_FROM} where {OWNED_BY} and i.id = $2",
        user.email,
        interview_id,
    )
    if row is None:
        raise NotFound(interview_id)

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
        interview=InterviewSummary.model_validate(dict(row)),
        results=[ResultField.model_validate(dict(r)) for r in results],
        events=[TranscriptEvent.model_validate(dict(e)) for e in events],
    )


async def patients(user: CurrentUser, *, limit: int | None = None) -> list[PatientSummary]:
    """The caller's own list, plus the unowned demo rows, carrying `origin` so
    the two are distinguishable on the screen."""
    rows = await _pool().fetch(
        "select p.id, p.first_name, p.origin, p.clinician_email, p.created_at, "
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
