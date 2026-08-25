"""Turning a bearer string into claims we believe.

Two things here are load-bearing and both are refusals. The algorithm list is
closed, which is what refuses a token that asks to be verified with `none` or
with the public key read as an HMAC secret. And `issuer` and `audience` are
always asserted, so a token minted by some other Supabase project — or by the
same project for some other audience — is not a token for us.
"""

from dataclasses import dataclass
from typing import Any

import jwt

from shared.auth.jwks import Jwks, UnknownKey

#: The only two Supabase signs with. Closed on purpose: an open list is how a
#: token signed `none`, or signed with the public key as an HMAC secret, gets
#: verified.
ALGORITHMS = ("ES256", "RS256")

#: What Supabase puts in `aud` for a signed-in user.
AUDIENCE = "authenticated"


class InvalidToken(Exception):
    """Anything that means we do not believe this token. One exception rather
    than a taxonomy: the caller is told 401 and nothing else, because telling an
    anonymous caller *which* check failed is telling them how to pass it."""


@dataclass(frozen=True)
class Claims:
    """What a verified token says. Not who the caller is — that is
    `config.accounts`, and the separation is the point."""

    sub: str
    email: str
    raw: dict[str, Any]


async def verify(token: str, *, keys: Jwks, issuer: str) -> Claims:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise InvalidToken("malformed token") from exc
    if not kid:
        raise InvalidToken("token names no signing key")

    try:
        key = await keys.key(kid)
    except UnknownKey as exc:
        raise InvalidToken("unknown signing key") from exc

    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=list(ALGORITHMS),
            audience=AUDIENCE,
            issuer=issuer,
            # Asserted rather than assumed: a token missing `exp` is a token
            # that never expires, and PyJWT will not require one on its own.
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise InvalidToken("token carries no email")
    return Claims(sub=str(payload["sub"]), email=email, raw=payload)
