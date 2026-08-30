"""Taking a red flag.

The fifth writer, and the smallest. Everything about a red flag is already in
the record — the gate scanned, `transcript.events` holds what it matched, and
`reads.py` derives the worst action on the call from those rows. The one fact
the record cannot produce on its own is *that a human has seen it*, and this
module writes exactly that fact and nothing else.

No disposition, no close reason, no second state. "I have this" is the whole
statement; what was decided belongs to the sign-off (5c) or to the practice's
own systems, and a column here claiming to hold it would be a column nothing
fills in honestly.

Per interview rather than per flag, so a call carrying two reds is cleared
once. Scoped by `reads.OWNED_BY`, spelled the same way as every read, because a
clinician clearing somebody else's escalation is the one thing this route must
not allow — and the scope has to reach the `update`'s `where`, not stop at the
door.
"""

from typing import Any

from services.core import db
from services.core.reads import OWNED_BY, NotFound
from shared.auth import CurrentUser
from shared.contracts.models import Acknowledgement

#: One statement, and `coalesce` is what makes it idempotent.
#:
#: A second POST re-writes the values it already found, so the first stamp and
#: the first acknowledger survive: acknowledging cannot quietly reassign who
#: owns the decision to whoever clicked last. Doing it in one statement rather
#: than a read then a write is what makes that true under two clicks at once —
#: the shape "select, decide, update" has a window between the first two where
#: both callers see null.
#:
#: There is no un-acknowledge. Nobody has needed one, and a reversible
#: acknowledgement is a different promise from the one the band makes.
_ACKNOWLEDGE = f"""
    update clinical.interviews i
       set acknowledged_at = coalesce(i.acknowledged_at, now()),
           acknowledged_by = coalesce(i.acknowledged_by, $1)
      from clinical.patients p
     where p.id = i.patient_id
       and i.id = $2
       and {OWNED_BY}
    returning i.id as interview_id, i.acknowledged_at, i.acknowledged_by
"""


def _pool() -> Any:
    pool = db.pool()
    if pool is None:
        raise RuntimeError("no database configured — there is nothing to acknowledge")
    return pool


async def acknowledge(user: CurrentUser, interview_id: str) -> Acknowledgement:
    """Stamp this interview as taken, and say who took it.

    An interview outside the caller's scope raises `NotFound`, exactly as a read
    of it would: a 403 would confirm the id names a real call, which is most of
    what an unauthorised caller wanted to learn.
    """
    row = await _pool().fetchrow(_ACKNOWLEDGE, user.email, interview_id)
    if row is None:
        raise NotFound(interview_id)
    return Acknowledgement.model_validate(dict(row))
