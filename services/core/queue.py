"""Dispatch: deciding whose interview this call is.

Three arrivals, one function. A patient following an emailed link carries a
token; a visitor to the public demo carries nothing; and both end at the same
`claim()`, which is the only transition that decides a call is happening. Phase
5 fills in the token branch against `clinical.invitations` and changes nothing
else here, because the claim is already written.

Without a database the mint is all that runs and the row is never written —
`make dev` on a laptop with nothing provisioned behaves as it always did.
"""

import os
import uuid

from services.core import db
from shared.contracts.models import Patient, QueuedInterview

#: What the demo dispatches. The warm-up by default because it is one question
#: and proves the machinery in thirty seconds; `proto_preop_check_v1` is the
#: real thing — two sections, five authored fields, all four red-flag actions. A knob
#: rather than a constant only until Phase 5, where the protocol is a column on
#: the interview a clinician queued and this line goes away.
DEMO_PROTOCOL_ID = os.environ.get("DEMO_PROTOCOL_ID", "proto_warmup_v1")

#: A visitor to the public demo is a synthetic patient, not a shared one.
#: Everyone being Alice was fine while the record evaporated; once the rows
#: persist it fills `clinical.patients` with hundreds of identical people
#: (deployment.md §4, blocker 6).
#:
#: What actually separates two rows is the patient id — the name is cosmetic,
#: and two visitors drawing the same one is a real caseload, not a bug. The list
#: is long enough that a dashboard is readable, and every name is short and
#: unambiguous out loud, because `_speak_opening` says it to the patient.
DEMO_NAMES = (
    "Alice", "Ben", "Chloe", "Dev", "Erin", "Femi", "Greta", "Hari",
    "Iris", "Jonas", "Kira", "Leo", "Maya", "Nadia", "Omar", "Priya",
    "Quinn", "Rosa", "Sam", "Tara", "Umar", "Vera", "Wes", "Zoe",
)


class UnknownInvitation(RuntimeError):
    """The link is expired, spent or was never ours."""


def synthetic_interview() -> QueuedInterview:
    """One ephemeral demo patient and their interview, as objects.

    Pure and synchronous on purpose: it is the shape of an interview, and
    callers that only need the shape — the prompt tests, `make dev` with no
    database — should not have to await a connection to get it.
    """
    suffix = uuid.uuid4().hex[:8]
    return QueuedInterview(
        id=f"iv_{uuid.uuid4().hex[:12]}",
        protocol_id=DEMO_PROTOCOL_ID,
        patient=Patient(
            id=f"pt_{suffix}",
            first_name=DEMO_NAMES[int(suffix, 16) % len(DEMO_NAMES)],
        ),
    )


async def resolve_interview(token: str | None = None) -> QueuedInterview:
    """Whose interview this is, claimed and ready to run."""
    if token is not None:
        return await _by_invitation(token)

    interview = synthetic_interview()
    if not db.enabled():
        return interview

    pool = db.pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "insert into clinical.patients (id, first_name, origin) values ($1, $2, 'demo')",
            interview.patient.id,
            interview.patient.first_name,
        )
        await conn.execute(
            "insert into clinical.interviews (id, protocol_id, patient_id) values ($1, $2, $3)",
            interview.id,
            interview.protocol_id,
            interview.patient.id,
        )
    await claim(interview.id)
    return interview


async def _by_invitation(token: str) -> QueuedInterview:
    """Phase 5. The token is hashed and looked up in `clinical.invitations`,
    which binds it to exactly one interview; the row is then claimed by the same
    `claim()` the demo path uses, which is why that function is written now.
    """
    raise UnknownInvitation("interview links are not issued yet")


async def claim(interview_id: str) -> bool:
    """`queued → running`. True if this caller is the one that starts the call.

    The transition, not the read, is what makes a call singular: two tabs opened
    from one emailed link both find a queued row, and exactly one of them may
    have it. `skip locked` means the loser is told no immediately rather than
    waiting behind a lock for an answer that is already decided.
    """
    if not db.enabled():
        return True
    claimed = await db.pool().fetchval(
        "update clinical.interviews set status = 'running', started_at = now() "
        "where id = (select id from clinical.interviews "
        "            where id = $1 and status = 'queued' for update skip locked) "
        "returning id",
        interview_id,
    )
    return claimed is not None
