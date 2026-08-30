"""The link, against a real Postgres.

The property worth having is the one the derivation exists for: a clinician who
clicks copy twice gets the same URL both times, because the first may already
be in a patient's inbox. Everything else here is a refusal — spent, revoked,
expired, unknown — and they are all the same answer to the caller, so they are
asserted one at a time here instead.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import uuid

import pytest

from services.core import config, db, invitations
from services.core.queue import UnknownInvitation, resolve_interview

pytestmark = pytest.mark.postgres


@pytest.fixture
async def live_db(pool):
    db.configure(pool)
    await db.seed_protocols()
    try:
        yield pool
    finally:
        db.configure(None)


async def an_interview(pool) -> str:
    """One queued interview for a patient nobody owns — the shape dispatch
    produces, minus the dispatch, so this file is about the link alone."""
    suffix = uuid.uuid4().hex[:10]
    patient_id, interview_id = f"pt_inv_{suffix}", f"iv_inv_{suffix}"
    await pool.execute(
        "insert into clinical.patients (id, first_name, origin) values ($1, 'Nadia', 'dispatched')",
        patient_id,
    )
    await pool.execute(
        "insert into clinical.interviews (id, protocol_id, patient_id) "
        "values ($1, 'proto_warmup_v1', $2)",
        interview_id,
        patient_id,
    )
    return interview_id


def token_of(url: str) -> str:
    return url.split("invite=", 1)[1]


# ─── minting ─────────────────────────────────────────────────────────────────


async def test_a_mint_returns_a_url_on_the_portal(live_db):
    invitation = await invitations.mint(live_db, await an_interview(live_db))

    assert invitation.url.startswith(f"{config.PORTAL_URL}/?invite=")
    assert invitation.channel == "link"
    assert invitation.expires_at is not None


async def test_the_token_is_never_stored(live_db):
    """A leak of `clinical.invitations` must not be a leak of working links."""
    interview_id = await an_interview(live_db)
    invitation = await invitations.mint(live_db, interview_id)
    token = token_of(invitation.url)

    row = await live_db.fetchrow(
        "select nonce, token_hash from clinical.invitations where interview_id = $1",
        interview_id,
    )
    assert token not in row["token_hash"]
    assert token not in row["nonce"]
    assert row["token_hash"] == invitations._hash(token)


async def test_a_second_mint_returns_the_same_link(live_db):
    """The whole reason the token is derived rather than drawn. A fresh link on
    every click would silently kill the one already sent to a patient."""
    interview_id = await an_interview(live_db)

    first = await invitations.mint(live_db, interview_id)
    second = await invitations.mint(live_db, interview_id)

    assert second.url == first.url
    assert await live_db.fetchval(
        "select count(*) from clinical.invitations where interview_id = $1", interview_id
    ) == 1


async def test_a_rotated_secret_does_not_return_the_old_link(live_db, monkeypatch):
    """Rotating the secret invalidates every outstanding link — which is what a
    secret is for. The check is that the old URL is not handed back as if it
    still worked: the row is revoked and a new one minted."""
    interview_id = await an_interview(live_db)
    first = await invitations.mint(live_db, interview_id)

    monkeypatch.setattr(invitations, "INVITE_SECRET", "a-different-secret")
    second = await invitations.mint(live_db, interview_id)

    assert second.url != first.url
    assert await invitations.spend(live_db, token_of(first.url)) is None
    assert await invitations.spend(live_db, token_of(second.url)) == interview_id


async def test_two_interviews_do_not_share_a_link(live_db):
    a = await invitations.mint(live_db, await an_interview(live_db))
    b = await invitations.mint(live_db, await an_interview(live_db))

    assert a.url != b.url


# ─── spending ────────────────────────────────────────────────────────────────


async def test_a_link_resolves_to_its_interview_and_claims_it(live_db):
    """The whole arrival path: the token names one interview, spends itself
    doing it, and leaves the row `running` — which is what makes the call
    singular."""
    interview_id = await an_interview(live_db)
    invitation = await invitations.mint(live_db, interview_id)

    interview = await resolve_interview(token_of(invitation.url))

    assert interview.id == interview_id
    assert interview.protocol_id == "proto_warmup_v1"
    assert interview.patient.first_name == "Nadia"
    assert await live_db.fetchval(
        "select status from clinical.interviews where id = $1", interview_id
    ) == "running"


async def test_a_second_use_is_refused(live_db):
    """The link is spent when the call starts. The same person coming back to
    the same link is the common case and it is still a refusal, because the
    interview it named has already begun."""
    invitation = await invitations.mint(live_db, await an_interview(live_db))
    token = token_of(invitation.url)
    await resolve_interview(token)

    with pytest.raises(UnknownInvitation):
        await resolve_interview(token)


async def test_a_spent_link_stops_being_the_live_one(live_db):
    """`invitations_live` excludes an opened row, so the next mint issues a new
    link rather than handing back one that no longer resolves."""
    interview_id = await an_interview(live_db)
    first = await invitations.mint(live_db, interview_id)
    await invitations.spend(live_db, token_of(first.url))

    assert (await invitations.mint(live_db, interview_id)).url != first.url


async def test_a_revoked_link_is_refused(live_db):
    interview_id = await an_interview(live_db)
    invitation = await invitations.mint(live_db, interview_id)
    await live_db.execute(
        "update clinical.invitations set revoked_at = now() where interview_id = $1",
        interview_id,
    )

    assert await invitations.spend(live_db, token_of(invitation.url)) is None


async def test_an_expired_link_is_refused(live_db):
    interview_id = await an_interview(live_db)
    invitation = await invitations.mint(live_db, interview_id)
    await live_db.execute(
        "update clinical.invitations set expires_at = now() - interval '1 day' "
        "where interview_id = $1",
        interview_id,
    )

    assert await invitations.spend(live_db, token_of(invitation.url)) is None


async def test_an_expired_link_is_replaced_rather_than_left_behind(live_db):
    """Expiry is not part of what `invitations_live` calls live, so the stale
    row is found by the next mint, revoked, and superseded — rather than
    colliding with the new one on the unique index."""
    interview_id = await an_interview(live_db)
    stale = await invitations.mint(live_db, interview_id)
    await live_db.execute(
        "update clinical.invitations set expires_at = now() - interval '1 day' "
        "where interview_id = $1",
        interview_id,
    )

    fresh = await invitations.mint(live_db, interview_id)

    assert fresh.url != stale.url
    assert await invitations.spend(live_db, token_of(fresh.url)) == interview_id


async def test_an_unknown_token_is_refused(live_db):
    assert await invitations.spend(live_db, "not-a-token-we-issued") is None


async def test_a_link_whose_call_already_started_is_refused(live_db):
    """`claim` is what decides a call is happening, and it is the last of the
    three refusals: the link was honoured, the interview was no longer queued."""
    interview_id = await an_interview(live_db)
    invitation = await invitations.mint(live_db, interview_id)
    await live_db.execute(
        "update clinical.interviews set status = 'running' where id = $1", interview_id
    )

    with pytest.raises(UnknownInvitation):
        await resolve_interview(token_of(invitation.url))
