"""Signing an interview, and the chain that makes it provable.

The sixth writer, and the only one that is irreversible by design — everything
else in this repo either states a fact that was already true (`store.py`,
`queue.py`) or can be redone (`invitations.py` rotates, `acknowledgements.py`
is idempotent). A signature is neither: it is a named clinician's own
statement, pinned to the record as it stood and to the signature before it, and
nothing here offers a way to take it back.

**The lock is a global mutex, not a row-level one, and that is deliberate.**
The plan this stage was built from said "take the head under `select ... for
update`" over the latest row of `clinical.signatures`. That does not actually
serialise two concurrent signers — a blocked `SELECT ... FOR UPDATE` re-reads
the row it was blocked on, and that row is never itself updated by the signer
who wins the race, so the loser would recompute the same `prev_hash` the
winner already used and rely on the unique constraint to fail loudly rather
than on the lock to queue it correctly. `clinical.ledger_head` is one row that
*is* updated by the winner, so `for update` on it does what the plan wanted:
every signature in the system, for any interview, is serialised behind it.
Signing is rare and irreversible, so paying for a global lock on the rare path
is the trade worth making for a chain that provably cannot fork.
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from services.core import db, reads
from shared.auth import CurrentUser
from shared.contracts.models import InterviewSummary, Signature, SignatureRequest

#: Not a real hash — the empty chain's `prev_hash`, fixed-width like every hash
#: this table holds so a reader never has to learn "genesis" is spelled
#: differently.
GENESIS = "0" * 64


class AlreadySigned(ValueError):
    """A second attempt to sign an interview that already carries a signature.

    Not idempotent, unlike `acknowledgements.py` — returning the first
    signature to a second caller would let a route answer 200 for an act that
    did not happen, and signing is exactly the act this repo is not willing to
    say happened twice."""


class NotSigned(LookupError):
    """A real, in-scope interview that nobody has signed yet."""


class NotReady(ValueError):
    """A queued or running interview — its own record is still being written
    under it, so there is nothing yet for a hash to be honest about."""


def _pool() -> Any:
    pool = db.pool()
    if pool is None:
        raise RuntimeError("no database configured — there is nothing to sign")
    return pool


def _age(born: date | None, at: date) -> int | None:
    if born is None:
        return None
    years = at.year - born.year
    if (at.month, at.day) < (born.month, born.day):
        years -= 1
    return years


def _issued_summary(row: InterviewSummary) -> str:
    """The one sentence that gets signed, composed here from columns a read
    already produced — never accepted from the client, and never the
    composer's invented referral clause.

    `frontend/dashboard/src/screens/Interview.tsx`'s live preview says what the
    call was *for* using `demo.ts:referral`, because a demo needs some context
    that this product does not actually collect. That is fine for a screen —
    it says so with a chip — and it is not fine for the one paragraph this
    repo is about to make irreversible. So this sentence says only what a
    query can prove: how old the patient is, which protocol ran, how it ended,
    and how much of it was captured.
    """
    age = _age(row.patient_date_of_birth, datetime.now(UTC).date())
    who = f"{age}-year-old" if age is not None else "patient"
    if row.outcome == "safety":
        ending = "stopped by the safety gate before generation"
    elif row.status == "completed":
        ending = "completed"
    else:
        ending = row.status
    when = f" at {row.ended_at:%Y-%m-%d %H:%M}" if row.ended_at else ""
    return (
        f"{who}, {row.protocol_label.lower()}. "
        f"Interview {ending}{when}; "
        f"{row.captured_fields} of {row.total_fields} declared items captured."
    )


def _record_hash(interview: dict, results: list[dict], events: list[dict]) -> str:
    """sha256 of the record as it stood the moment it was signed.

    Plain `json.dumps(..., sort_keys=True, default=str)` rather than a
    hand-rolled concatenation: every value already round-trips through `str`
    (every timestamp here is one), and the result is a document an auditor
    could reproduce from the same three queries this function runs, without
    reading a line of this file.
    """
    document = {"interview": interview, "results": results, "events": events}
    text = json.dumps(document, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


async def sign(user: CurrentUser, interview_id: str, request: SignatureRequest) -> Signature:
    """Sign one interview, chained to the ledger's current head.

    Scoped by the read: `reads.summary` raises `NotFound` for an interview
    outside this caller's caseload, exactly as reading it would — a 403 would
    confirm the id names a real call.
    """
    row = await reads.summary(user, interview_id)
    if row.status not in ("completed", "abandoned"):
        raise NotReady("an interview must have ended before it can be signed")

    pool = _pool()
    async with pool.acquire() as conn, conn.transaction():
        # Every signer in the system blocks here — see the module docstring.
        # Nothing else may proceed past this line until this transaction ends.
        head = await conn.fetchval(
            "select hash from clinical.ledger_head where id = true for update"
        )

        # Checked *after* the lock, not before: a second sign of this same
        # interview racing this one would otherwise see no row yet and both
        # proceed, relying on `interview_id unique` to catch it after the
        # fact. Behind the global lock, the check is atomic with the insert.
        already = await conn.fetchval(
            "select 1 from clinical.signatures where interview_id = $1", interview_id
        )
        if already:
            raise AlreadySigned(interview_id)

        interview = await conn.fetchrow(
            "select id, protocol_id, patient_id, status, outcome, "
            "scheduled_for, started_at, ended_at, created_at "
            "from clinical.interviews where id = $1",
            interview_id,
        )
        results = await conn.fetch(
            "select field_key, label, value, status, updated_at "
            "from clinical.results where interview_id = $1 order by field_key",
            interview_id,
        )
        events = await conn.fetch(
            "select seq, type, at, payload from transcript.events "
            "where interview_id = $1 order by seq",
            interview_id,
        )

        record_hash = _record_hash(
            dict(interview), [dict(r) for r in results], [dict(e) for e in events]
        )
        issued_summary = _issued_summary(row)
        signed_at = datetime.now(UTC)
        chained = [
            head,
            record_hash,
            request.impression,
            request.disposition,
            user.email,
            signed_at.isoformat(),
        ]
        hash_ = hashlib.sha256("|".join(chained).encode()).hexdigest()

        await conn.execute(
            "insert into clinical.signatures "
            "(id, interview_id, prev_hash, record_hash, hash, issued_summary, "
            " impression, disposition, signed_by, signed_at) "
            "values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            f"sig_{uuid.uuid4().hex[:12]}",
            interview_id,
            head,
            record_hash,
            hash_,
            issued_summary,
            request.impression,
            request.disposition,
            user.email,
            signed_at,
        )
        # The winner moves the pointer before releasing the lock, which is the
        # whole of what makes the next signer's read of it correct.
        await conn.execute("update clinical.ledger_head set hash = $1 where id = true", hash_)

    return Signature(
        interview_id=interview_id,
        prev_hash=head,
        record_hash=record_hash,
        hash=hash_,
        issued_summary=issued_summary,
        impression=request.impression,
        disposition=request.disposition,
        signed_by=user.email,
        signed_at=signed_at,
    )


async def verify(pool: Any, interview_id: str) -> bool:
    """Recompute this interview's signature from the record as it stands right
    now, and confirm both that the hash matches and that it chains correctly
    to its predecessor.

    Not exposed over the API — an integrity check for tests and an operator's
    own tooling, not a clinical read, and unscoped for the same reason: proving
    the chain has not been tampered with is a question about the database, not
    about one caller's caseload.
    """
    sig = await pool.fetchrow(
        "select * from clinical.signatures where interview_id = $1", interview_id
    )
    if sig is None:
        return False

    interview = await pool.fetchrow(
        "select id, protocol_id, patient_id, status, outcome, "
        "scheduled_for, started_at, ended_at, created_at "
        "from clinical.interviews where id = $1",
        interview_id,
    )
    results = await pool.fetch(
        "select field_key, label, value, status, updated_at "
        "from clinical.results where interview_id = $1 order by field_key",
        interview_id,
    )
    events = await pool.fetch(
        "select seq, type, at, payload from transcript.events where interview_id = $1 order by seq",
        interview_id,
    )

    record_hash = _record_hash(
        dict(interview), [dict(r) for r in results], [dict(e) for e in events]
    )
    if record_hash != sig["record_hash"]:
        return False

    expected = hashlib.sha256(
        "|".join(
            [
                sig["prev_hash"],
                record_hash,
                sig["impression"],
                sig["disposition"],
                sig["signed_by"],
                sig["signed_at"].isoformat(),
            ]
        ).encode()
    ).hexdigest()
    if expected != sig["hash"]:
        return False

    if sig["prev_hash"] == GENESIS:
        return True
    predecessor = await pool.fetchval(
        "select 1 from clinical.signatures where hash = $1", sig["prev_hash"]
    )
    return predecessor is not None


async def ledger(user: CurrentUser, interview_id: str) -> Signature:
    """The signature for one interview, read back exactly as it was written.

    Two refusals, told apart: `reads.summary` raises `NotFound` for an
    interview outside scope or that does not exist, and this raises
    `NotSigned` for a real, visible interview nobody has signed — the second
    is not a secret the way the first is, so it earns its own sentence.
    """
    await reads.summary(user, interview_id)
    row = await _pool().fetchrow(
        "select interview_id, prev_hash, record_hash, hash, issued_summary, "
        "impression, disposition, signed_by, signed_at "
        "from clinical.signatures where interview_id = $1",
        interview_id,
    )
    if row is None:
        raise NotSigned(interview_id)
    return Signature.model_validate(dict(row))
