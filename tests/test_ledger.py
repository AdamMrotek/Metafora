"""Signing an interview, against a real Postgres.

`clinical.signatures` and `clinical.ledger_head`'s constraints are exercised in
`tests/test_schema.py` — this file is `ledger.py`'s own logic: the chain
extends from whatever the head actually is rather than from a hardcoded
genesis (the scratch database is shared across the postgres suite, and this
file is not the only one that may write to it), a tampered record is
detectable, a second sign is refused rather than silently accepted, an
unfinished call cannot be signed, and two signers racing each other still
produce a chain instead of a fork.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import asyncio

import pytest

from services.core import db, ledger, reads
from shared.contracts.models import SignatureRequest

pytestmark = pytest.mark.postgres

ALICE = "alice@example.test"
BOB = "bob@example.test"
PROTOCOL = "proto_warmup_v1"


def user(email: str = ALICE):
    from shared.auth import CurrentUser

    return CurrentUser(email=email, sub="auth-user", role="clinician", display_name="Test")


def request(**kwargs) -> SignatureRequest:
    return SignatureRequest(
        impression=kwargs.pop("impression", "Settled, no concerns raised."),
        disposition=kwargs.pop("disposition", "same_day"),
    )


@pytest.fixture
async def live_db(pool):
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
    pool, *, owner: str | None = ALICE, status: str = "completed", first_name: str = "Nadia"
) -> str:
    """A finished interview with one captured field and one transcript line —
    enough for `_record_hash` to have something to hash."""
    n = next(_seq)
    patient_id, interview_id = f"pt_sig_{n}", f"iv_sig_{n}"
    await pool.execute(
        "insert into clinical.patients (id, first_name, origin, clinician_email) "
        "values ($1, $2, 'dispatched', $3)",
        patient_id,
        first_name,
        owner,
    )
    await pool.execute(
        "insert into clinical.interviews "
        "(id, protocol_id, patient_id, status, started_at, ended_at) "
        "values ($1, $2, $3, $4, now(), now())",
        interview_id,
        PROTOCOL,
        patient_id,
        status,
    )
    await pool.execute(
        "insert into clinical.results (interview_id, field_key, label, value, status) "
        "values ($1, 'greeting', 'Greeting', 'fine thanks', 'captured')",
        interview_id,
    )
    await pool.execute(
        "insert into transcript.events (interview_id, session_id, seq, type, at, payload) "
        "values ($1, $2, 1, 'turn', now(), $3)",
        interview_id,
        f"sess_sig_{n}",
        {"role": "agent", "text": "Hello"},
    )
    return interview_id


# ─── the chain ────────────────────────────────────────────────────────────────


async def test_a_signature_chains_to_the_current_head(live_db):
    head_before = await live_db.fetchval("select hash from clinical.ledger_head where id = true")
    interview_id = await a_call(live_db)

    sig = await ledger.sign(user(), interview_id, request())

    assert sig.prev_hash == head_before
    head_after = await live_db.fetchval("select hash from clinical.ledger_head where id = true")
    assert head_after == sig.hash


async def test_a_second_signature_chains_to_the_first(live_db):
    a = await a_call(live_db)
    b = await a_call(live_db)

    first = await ledger.sign(user(), a, request())
    second = await ledger.sign(user(), b, request())

    assert second.prev_hash == first.hash
    assert second.hash != first.hash


async def test_concurrent_signs_do_not_fork(live_db):
    """The property the global lock exists for: two signers racing each other
    still produce one chain, not two links both claiming the same parent."""
    head_before = await live_db.fetchval("select hash from clinical.ledger_head where id = true")
    a = await a_call(live_db)
    b = await a_call(live_db)

    sig_a, sig_b = await asyncio.gather(
        ledger.sign(user(), a, request()), ledger.sign(user(), b, request())
    )

    assert sig_a.hash != sig_b.hash
    prevs = {sig_a.prev_hash, sig_b.prev_hash}
    hashes = {sig_a.hash, sig_b.hash}
    # One of them extended the chain as it stood before either ran; the other
    # extended from whichever went first. Both claiming `head_before` — a
    # fork — is exactly what the global lock exists to prevent.
    assert head_before in prevs
    assert (prevs - {head_before}) <= hashes


# ─── verification ─────────────────────────────────────────────────────────────


async def test_a_freshly_signed_record_verifies(live_db):
    interview_id = await a_call(live_db)
    await ledger.sign(user(), interview_id, request())

    assert await ledger.verify(live_db, interview_id) is True


async def test_a_tampered_record_fails_verification(live_db):
    """`clinical.results` carries no append-only trigger — nothing stops a
    later process from editing it — which is exactly what the record hash
    exists to catch."""
    interview_id = await a_call(live_db)
    await ledger.sign(user(), interview_id, request())

    await live_db.execute(
        "update clinical.results set value = 'tampered' where interview_id = $1", interview_id
    )

    assert await ledger.verify(live_db, interview_id) is False


async def test_an_unsigned_interview_does_not_verify(live_db):
    interview_id = await a_call(live_db)
    assert await ledger.verify(live_db, interview_id) is False


# ─── refusals ─────────────────────────────────────────────────────────────────


async def test_signing_twice_is_refused(live_db):
    interview_id = await a_call(live_db)
    await ledger.sign(user(), interview_id, request())

    with pytest.raises(ledger.AlreadySigned):
        await ledger.sign(user(), interview_id, request(impression="a second attempt"))


async def test_an_unfinished_interview_cannot_be_signed(live_db):
    interview_id = await a_call(live_db, status="running")

    with pytest.raises(ledger.NotReady):
        await ledger.sign(user(), interview_id, request())


async def test_a_stranger_cannot_sign_your_interview(live_db):
    """Scoped through `reads.summary`, exactly like a read — a 403 would
    confirm the id names a real call."""
    his = await a_call(live_db, owner=BOB)

    with pytest.raises(reads.NotFound):
        await ledger.sign(user(ALICE), his, request())


async def test_signing_an_interview_that_does_not_exist_is_not_found(live_db):
    with pytest.raises(reads.NotFound):
        await ledger.sign(user(), "iv_never_existed", request())


# ─── reading it back ──────────────────────────────────────────────────────────


async def test_reading_the_ledger_returns_what_was_written(live_db):
    interview_id = await a_call(live_db)
    written = await ledger.sign(user(), interview_id, request())

    assert await ledger.ledger(user(), interview_id) == written


async def test_an_unsigned_interview_has_no_ledger_entry(live_db):
    interview_id = await a_call(live_db)

    with pytest.raises(ledger.NotSigned):
        await ledger.ledger(user(), interview_id)


async def test_a_stranger_cannot_read_your_ledger_entry(live_db):
    his = await a_call(live_db, owner=BOB)
    await ledger.sign(user(BOB), his, request())

    with pytest.raises(reads.NotFound):
        await ledger.ledger(user(ALICE), his)


# ─── what reaches the dashboard's reads ───────────────────────────────────────


async def test_a_signed_interview_carries_its_signature_on_the_detail(live_db):
    interview_id = await a_call(live_db)
    sig = await ledger.sign(user(), interview_id, request())

    detail = await reads.interview(user(), interview_id)

    assert detail.signature == sig


async def test_an_unsigned_interview_carries_no_signature(live_db):
    interview_id = await a_call(live_db)

    detail = await reads.interview(user(), interview_id)

    assert detail.signature is None


async def test_the_summary_carries_when_it_was_signed(live_db):
    interview_id = await a_call(live_db)
    sig = await ledger.sign(user(), interview_id, request())

    row = await reads.summary(user(), interview_id)

    assert row.signed_at == sig.signed_at
