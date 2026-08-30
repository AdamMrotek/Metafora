"""Dispatch: deciding whose interview this call is.

Two arrivals, one function. A patient following a dispatched link carries a
token; a visitor to the public demo carries nothing; and both end at the same
`claim()`, which is the only transition that decides a call is happening. The
token branch was filled in at Phase 5a against `clinical.invitations` and
changed nothing else here, because the claim was already written.

With a database the demo arrival attaches its interview to one of the ten
seeded roster patients rather than creating an eleventh person; without one the
mint is all that runs and no row is written, so `make dev` on a laptop with
nothing provisioned behaves as it always did.
"""

import os
import uuid

from services.core import db, invitations
from shared.contracts.models import Patient, QueuedInterview

#: What the demo dispatches. The warm-up by default because it is one question
#: and proves the machinery in thirty seconds; `proto_preop_check_v1` is the
#: real thing — two sections, five authored fields, all four red-flag actions. A knob
#: rather than a constant only until Phase 5, where the protocol is a column on
#: the interview a clinician queued and this line goes away.
DEMO_PROTOCOL_ID = os.environ.get("DEMO_PROTOCOL_ID", "proto_warmup_v1")

#: The names the no-database path draws from. Every one is short and
#: unambiguous out loud, because `_speak_opening` says it to the patient.
#:
#: With a database the roster in `supabase/migrations/*_patient_identity.sql`
#: is used instead and this is not reached — a visitor takes a call *as* one of
#: ten seeded people rather than becoming an eleventh. That is what stops the
#: table filling with hundreds of one-call strangers (deployment.md §4,
#: blocker 6), and it is why those ten have an NHS number and a date of birth
#: while a minted one does not.
DEMO_NAMES = (
    "Alice", "Ben", "Chloe", "Dev", "Erin", "Femi", "Gwen", "Hari",
    "Iris", "Jonas", "Kira", "Leo", "Maya", "Nadia", "Omar", "Priya",
    "Quinn", "Rosa", "Sam", "Tara", "Umar", "Vera", "Wes", "Zoe",
)


#: Which demo rows a visitor may arrive as. A seeded identity *is* the
#: definition of a roster patient — the ten rows the migration wrote are the
#: only ones with an NHS number, and the per-visitor rows an older deployment
#: already minted have none, so they keep their history and stop growing.
#:
#: `random()` rather than round-robin because the alternative is a counter
#: somewhere, and ten rows read as a caseload whichever order they fill.
ROSTER_PICK = """
    select id, first_name from clinical.patients
    where origin = 'demo' and nhs_number is not null
    order by random() limit 1
"""


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
        roster = await conn.fetchrow(ROSTER_PICK)
        if roster is not None:
            interview = interview.model_copy(
                update={"patient": Patient(id=roster["id"], first_name=roster["first_name"])}
            )
        else:
            # A database with the schema but not the roster migration — an older
            # deployment mid-upgrade. Mint as before rather than refuse the call.
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


#: The interview a spent token named, in the shape the call needs. Read after
#: the invitation is claimed rather than joined into that statement, because the
#: statement that spends a link has to stay one conditional UPDATE.
_BY_ID = """
    select i.id, i.protocol_id, p.id as patient_id, p.first_name
    from clinical.interviews i
    join clinical.patients p on p.id = i.patient_id
    where i.id = $1
"""


async def _by_invitation(token: str) -> QueuedInterview:
    """The interview one link names, spent and claimed.

    Three refusals, all of them the same sentence to the caller. The token is
    not ours (or is revoked, expired or already opened): `invitations.spend`
    answers None. The interview vanished under it: no row. It is no longer
    queued: `claim` says no, which is a link whose call has already started —
    the second tab opened from an email, or the same person coming back.

    The order matters. Spending happens first and in one statement, so two
    arrivals cannot both be the first; the claim then decides which of them
    actually starts a call. A link is therefore spent even when the claim
    fails, which is right: the link was for one call, and that call has begun.
    """
    if not db.enabled():
        # No database means no invitations table, so no token can be ours. The
        # demo path still works — that is what `make dev` on a bare laptop is.
        raise UnknownInvitation("this deployment issues no interview links")

    pool = db.pool()
    interview_id = await invitations.spend(pool, token)
    if interview_id is None:
        raise UnknownInvitation("this link has expired or has already been used")

    row = await pool.fetchrow(_BY_ID, interview_id)
    if row is None:
        raise UnknownInvitation("this link has expired or has already been used")

    if not await claim(interview_id):
        raise UnknownInvitation("this link has expired or has already been used")

    return QueuedInterview(
        id=row["id"],
        protocol_id=row["protocol_id"],
        patient=Patient(id=row["patient_id"], first_name=row["first_name"]),
    )


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
