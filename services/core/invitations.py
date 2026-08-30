"""The link a patient follows, minted and spent.

One idea, and everything here is a consequence of it: the token is **derived**
from the row rather than drawn at random.

    token = base64url(hmac_sha256(INVITE_SECRET, f"{interview_id}:{nonce}"))

The roadmap says the token is stored hashed, and a hash cannot be un-hashed —
so "copy the link again" would normally have to mint a second link, silently
killing the one already sent to a patient. Deriving it instead means the same
row always yields the same URL: the nonce is stored in the clear because it is
not a secret, the token is stored only as `sha256(token)`, and the secret is in
the environment and never in the table. A leak of `clinical.invitations` yields
no working link, and a second click yields the link already sent.

Rotating `INVITE_SECRET` invalidates every outstanding link. That is what a
secret is for, and it is checked rather than assumed: `mint` re-hashes what it
re-derived before handing it back, so a link that no longer resolves is
replaced instead of returned.
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from services.core.config import INVITE_SECRET, PORTAL_URL
from shared.contracts.models import Invitation, InvitationChannel

#: How long a link lives. A scheduled call is days out and the link is sent
#: ahead of it, so anything much shorter is a support request rather than a
#: safeguard; anything much longer is a credential nobody remembers issuing.
INVITE_TTL = timedelta(days=14)

#: The one live invitation for an interview — at most one, by the partial unique
#: index `invitations_live`. Unspent and unrevoked is what "live" means; expiry
#: is deliberately not part of it, so an expired row is *found* here and
#: replaced, rather than left behind for the index to trip over.
_LIVE = """
    select id, nonce, token_hash, channel, expires_at
    from clinical.invitations
    where interview_id = $1 and revoked_at is null and opened_at is null
"""


def _derive(interview_id: str, nonce: str) -> str:
    """The token itself. 32 bytes of HMAC, base64url, 43 characters, no padding."""
    mac = hmac.new(
        INVITE_SECRET.encode(), f"{interview_id}:{nonce}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _hash(token: str) -> str:
    """What the table stores. Plain SHA-256: the token is 256 bits of HMAC
    output, so there is nothing to stretch — it is not a password and no amount
    of guessing narrows it."""
    return hashlib.sha256(token.encode()).hexdigest()


def _url(token: str) -> str:
    """A query parameter, not a path. `frontend/call/vercel.json` has no SPA
    rewrite, and a query needs none."""
    return f"{PORTAL_URL}/?invite={token}"


async def mint(
    pool: Any, interview_id: str, *, channel: InvitationChannel = "link"
) -> Invitation:
    """The link for this interview: the live one if there is one, else a new one.

    Reuse is the point. A clinician who clicks copy twice has to get the same
    URL both times, because the first one may already be in a patient's inbox —
    so the second click re-derives from the stored nonce rather than minting.
    It re-hashes before returning, so the one case where the old link is *not*
    honoured — a rotated secret — revokes and re-mints instead of handing back
    a URL that resolves to nothing.
    """
    async with pool.acquire() as conn, conn.transaction():
        live = await conn.fetchrow(_LIVE, interview_id)

        if live is not None:
            token = _derive(interview_id, live["nonce"])
            fresh = _hash(token) == live["token_hash"]
            expired = live["expires_at"] is not None and live["expires_at"] <= datetime.now(UTC)
            if fresh and not expired:
                return Invitation(
                    url=_url(token),
                    interview_id=interview_id,
                    channel=live["channel"],
                    expires_at=live["expires_at"],
                )
            # Either the secret rotated under it or its window closed. Revoke
            # rather than update: `invitations_live` admits one live row per
            # interview, and the history of what was issued is worth keeping.
            await conn.execute(
                "update clinical.invitations set revoked_at = now() where id = $1", live["id"]
            )

        nonce = secrets.token_urlsafe(16)
        token = _derive(interview_id, nonce)
        expires_at = datetime.now(UTC) + INVITE_TTL
        await conn.execute(
            "insert into clinical.invitations "
            "(id, interview_id, nonce, token_hash, channel, expires_at) "
            "values ($1, $2, $3, $4, $5, $6)",
            f"inv_{uuid.uuid4().hex[:12]}",
            interview_id,
            nonce,
            _hash(token),
            channel,
            expires_at,
        )

    return Invitation(
        url=_url(token),
        interview_id=interview_id,
        channel=channel,
        expires_at=expires_at,
    )


async def spend(pool: Any, token: str) -> str | None:
    """Claim this token, and return the interview it names — or None.

    One statement, and that is the whole safety property. Selecting the row and
    then stamping it would let two tabs opened from the same link both find it
    unspent; a conditional UPDATE ... RETURNING makes exactly one of them the
    first, and the loser is indistinguishable from someone holding a link that
    was never ours.

    Every refusal — unknown, revoked, expired, already opened — is the same
    None. A caller learns whether their link works and nothing else; telling
    them *which* of those it was is telling them a link exists.
    """
    return await pool.fetchval(
        "update clinical.invitations set opened_at = now() "
        "where token_hash = $1 and revoked_at is null and opened_at is null "
        "  and (expires_at is null or expires_at > now()) "
        "returning interview_id",
        _hash(token),
    )
