"""The door.

One rule holds this package together: **no route body ever writes
`if user.role == ...`**. A role check that lives in a handler is a role check
nobody can find later, and finding all of them is exactly what moving to rules
over relationships (roadmap §6+) will require. So the decision is made here and
the route only names which decision it wants.

The dependency yields a `CurrentUser`, and the read functions in
`services/core/reads.py` take it as a parameter. Identity reaching the SQL — not
just the door — is the other half of the same bet: a system that authorises at
the door and then queries unscoped cannot be retrofitted with scoping.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from shared.auth.accounts import AccountSource, CurrentUser, Denied, DirectoryUnavailable
from shared.auth.jwks import Jwks
from shared.auth.verify import InvalidToken, verify

#: Every role the seed may grant, and therefore every role that reads the
#: clinical record. Named once so the day a third role appears is a change here
#: rather than a search through three routers.
READS_THE_RECORD = ("clinician", "admin")

#: Said to a caller who brought nothing, or brought something we do not believe.
#: Deliberately the same sentence for both: which check failed is not something
#: an anonymous caller is owed.
UNAUTHENTICATED = "sign in to read this"

#: Said when the process has no way to answer the question at all — no
#: SUPABASE_URL, so nothing verifies, or no database, so there is nowhere to
#: look an account up. A refusal, never a bypass, and deliberately not a 403:
#: telling a clinician with a good account that they have none would send them
#: off to fix the one thing that is not broken.
UNCONFIGURED = "authentication is not configured on this server"


@dataclass(frozen=True)
class Authenticator:
    """Everything the door needs, assembled once in `lifespan`."""

    jwks: Jwks
    issuer: str
    accounts: AccountSource

    async def identify(self, token: str) -> CurrentUser:
        claims = await verify(token, keys=self.jwks, issuer=self.issuer)
        return await self.accounts.resolve(claims)


_authenticator: Authenticator | None = None


def configure(authenticator: Authenticator | None) -> None:
    """Install the authenticator, or `None` to make every guarded route 503.

    `None` is the dev default and it is not a hole: a process that cannot verify
    a token refuses every request that would need one.
    """
    global _authenticator
    _authenticator = authenticator


def configured() -> bool:
    return _authenticator is not None


def bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


async def current_user(request: Request) -> CurrentUser:
    """Verified, and named in `config.accounts`. Not yet checked for a role.

    The order of the two refusals below is deliberate. Whether this process can
    verify anything at all is a fact about the server, not about the caller, so
    it is answered first: a box with no `SUPABASE_URL` says 503 to everyone,
    including someone holding a perfectly good token. Reading the header first
    would answer that caller 401 and send them off to fix a credential that was
    never the problem.
    """
    authenticator = _authenticator
    if authenticator is None:
        raise HTTPException(503, UNCONFIGURED)
    token = bearer(request)
    try:
        return await authenticator.identify(token)
    except InvalidToken as exc:
        # 401, and the reason stays in the log. The token did not verify, so we
        # are not talking to anyone we can name.
        raise HTTPException(
            401, UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    except DirectoryUnavailable as exc:
        # A fact about the server, so it gets the server's answer — the same one
        # an unconfigured process gives above.
        raise HTTPException(503, UNCONFIGURED) from exc
    except Denied as exc:
        # 403 *with* the reason. This caller proved who they are and the answer
        # is about them, not about the token — `app.py`'s `_error_body` puts it
        # where the frontend reads it.
        raise HTTPException(403, str(exc)) from exc


def require_role(*roles: str):
    """A FastAPI dependency admitting exactly these roles."""
    allowed = set(roles)

    async def guard(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in allowed:
            wanted = ", ".join(sorted(allowed))
            raise HTTPException(
                403, f"{user.email} is a {user.role}; this needs one of: {wanted}"
            )
        return user

    return guard


#: How a clinical route asks for the caller — as an annotation, so the role set
#: is decided here and a handler only spells the name.
#:
#: This is the shape of the rule that replaces it. When authorisation becomes a
#: question about a relationship rather than a role, this alias is what changes;
#: the routes that use it do not.
ClinicalReader = Annotated[CurrentUser, Depends(require_role(*READS_THE_RECORD))]
