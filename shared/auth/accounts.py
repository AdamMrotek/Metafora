"""Who a verified token *is*, which is a different question from whether it verifies.

Supabase will happily sign a token for anyone who completes a sign-up, and on a
public demo URL that is everyone. `config.accounts` is the second answer: a row
the migration wrote, naming an address and what it may do. Verifying proves the
holder controls that mailbox; this table is what turns that into a caseload.

Nothing here inserts. The one write is binding `sub` the first time a verified
token arrives for a seeded email, which is what stops a second auth user from
later claiming the same address.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from shared.auth.verify import Claims


@dataclass(frozen=True)
class CurrentUser:
    """The identity every authorised route is handed — and passes on.

    Query functions take this as a parameter rather than reading a role at the
    door and then running unscoped SQL. That habit is the whole of what makes
    the move to per-relationship rules a `where` clause later instead of an
    audit of every route.
    """

    email: str
    sub: str
    role: str
    display_name: str


class Denied(Exception):
    """Verified, but nobody here. Carries a reason, because unlike a failed
    signature this one is safe to explain: the caller proved who they are, and
    "your address is not on the list" is the only useful thing to say next."""


class DirectoryUnavailable(Exception):
    """There is nowhere to look the caller up.

    Distinct from `Denied` because it is a fact about this process, not about
    them: answering 403 would tell a clinician with a perfectly good account
    that they do not have one, and send them off to fix the wrong thing.
    """


class AccountSource(Protocol):
    async def resolve(self, claims: Claims) -> CurrentUser: ...


class PostgresAccounts:
    """`config.accounts`, over whatever pool the process already has.

    Takes a *getter* rather than a pool: `services/core/db.py` owns the one
    pool, opens it in `lifespan`, and closes it on the way out. Holding the
    object here would mean holding a closed pool after a reconnect.
    """

    def __init__(self, pool_getter) -> None:
        self._pool_getter = pool_getter

    async def resolve(self, claims: Claims) -> CurrentUser:
        pool: Any = self._pool_getter()
        if pool is None:
            raise DirectoryUnavailable("no database — there is nowhere to look an account up")

        row = await pool.fetchrow(
            "select email, sub, role, display_name from config.accounts where email = $1",
            claims.email,
        )
        if row is None:
            raise Denied(f"{claims.email} is not a Metafora account")

        if row["sub"] is None:
            # First verified sign-in for a seeded row. Conditional on `sub is
            # null` in SQL, not on the read above, so two tabs signing in at
            # once cannot both think they were first.
            await pool.execute(
                "update config.accounts set sub = $2 where email = $1 and sub is null",
                claims.email,
                claims.sub,
            )
        elif row["sub"] != claims.sub:
            raise Denied(f"{claims.email} is bound to a different sign-in")

        return CurrentUser(
            email=row["email"],
            sub=claims.sub,
            role=row["role"],
            display_name=row["display_name"],
        )
