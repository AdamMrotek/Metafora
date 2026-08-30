"""Queueing an interview for a named person.

The first thing in this repo that writes `clinical.patients.clinician_email`.
Until now the column existed and nothing set it, so `reads.OWNED_BY` was
"everyone sees the unowned demo rows" and the scope predicate had nothing to
discriminate. Being dispatched a call is what makes someone yours, and this is
where that happens.

One transaction, because a patient with no interview is a person nobody called
and an interview with no patient cannot exist — the foreign key says so. The
summary is read back afterwards through `reads.summary`, so the row the
composer draws the instant it succeeds is the row the review table draws after
a reload, produced by one query rather than assembled twice.
"""

import re
import uuid
from typing import Any

from services.agent.config.protocol import PROTOCOLS
from services.core import db, reads
from services.core.reads import OWNED_BY, NotFound
from shared.auth import CurrentUser
from shared.contracts.models import DispatchRequest, InterviewSummary

#: What the assistant will say out loud in its first sentence, so it has to be
#: a name and not a form field. Letters, spaces, hyphens and apostrophes; the
#: length is the practical one for a spoken greeting.
_NAME = re.compile(r"^[^\W\d_][^\d_]{0,39}$", re.UNICODE)


class Refused(ValueError):
    """The request cannot be honoured, and the reason is safe to say back —
    an unknown protocol, a name that is not one, neither or both of the two
    ways to name a patient."""


def _pool() -> Any:
    pool = db.pool()
    if pool is None:
        # `config.py` refuses to boot outside dev without DATABASE_URL, so this
        # is `make dev` on a laptop with nothing provisioned: there is nowhere
        # to queue anything, and saying so beats a stack trace.
        raise Refused("this deployment has no database, so there is nothing to dispatch to")
    return pool


def _clean(request: DispatchRequest) -> tuple[str | None, str | None]:
    """Exactly one of the two ways to name a patient, validated."""
    patient_id = (request.patient_id or "").strip() or None
    first_name = (request.first_name or "").strip() or None

    if patient_id and first_name:
        raise Refused("name a patient or a new first name, not both")
    if not patient_id and not first_name:
        raise Refused("a call needs somebody to be for")
    if first_name and not _NAME.match(first_name):
        raise Refused("a first name is what the assistant says out loud — letters, no digits")
    return patient_id, first_name


async def create_interview(user: CurrentUser, request: DispatchRequest) -> InterviewSummary:
    """Queue one interview, and return the row the review table draws for it."""
    if request.protocol_id not in PROTOCOLS:
        raise Refused(f"no protocol {request.protocol_id}")
    patient_id, first_name = _clean(request)

    pool = _pool()
    interview_id = f"iv_{uuid.uuid4().hex[:12]}"

    async with pool.acquire() as conn, conn.transaction():
        if patient_id is not None:
            # The same predicate the reads use, spelled the same way. A patient
            # outside the caller's scope is a `NotFound` and not a 403, because
            # a 403 would confirm the id names somebody.
            #
            # Ownership is deliberately *not* claimed here. Dispatching a call
            # to one of the shared demo roster would otherwise take that person
            # off every other clinician's dashboard, and being sent a call by
            # somebody does not make a person theirs retrospectively.
            owned = await conn.fetchval(
                f"select p.id from clinical.patients p where p.id = $2 and {OWNED_BY}",
                user.email,
                patient_id,
            )
            if owned is None:
                raise NotFound(patient_id)
        else:
            patient_id = f"pt_{uuid.uuid4().hex[:12]}"
            # `origin` and `clinician_email` together are the whole difference
            # between a caseload and a visitor log: this row was dispatched to,
            # by this clinician, and both facts are on it from the first insert.
            await conn.execute(
                "insert into clinical.patients (id, first_name, origin, clinician_email) "
                "values ($1, $2, 'dispatched', $3)",
                patient_id,
                first_name,
                user.email,
            )

        # `queued`, which is the default — spelled out because `queue.claim()`
        # is the only transition out of it and this is the row it will find.
        await conn.execute(
            "insert into clinical.interviews "
            "(id, protocol_id, patient_id, status, scheduled_for) "
            "values ($1, $2, $3, 'queued', $4)",
            interview_id,
            request.protocol_id,
            patient_id,
            request.scheduled_for,
        )

    # Read back through the query the review table already uses, rather than
    # assembling a second summary here out of what was just inserted.
    return await reads.summary(user, interview_id)
