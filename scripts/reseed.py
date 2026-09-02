"""Delete the seeded demo calls so the next boot writes them again.

    uv run python scripts/reseed.py                  # every seeded call
    uv run python scripts/reseed.py --only iv_demo_07
    uv run python scripts/reseed.py --yes            # no confirmation

`services/core/seed.py` is idempotent by `on conflict do nothing`, which is what
makes a re-boot cheap and is also why it can never *correct* a row it already
wrote. Editing the seed — renaming a patient, rewording a question, adding a
turn — therefore changes nothing about a database that has already run it. This
removes the rows so the seed writes them afresh.

Scoped to `iv_demo_%`, the ids `seed.py` mints. A call somebody actually took is
never touched, which is the whole reason those ids look the way they do.

**It suspends `events_immutable`.** `transcript.events` refuses DELETE by
trigger, and stepping around that is the one thing this file does that wants
saying out loud. The justification is the same as
`supabase/migrations/20260829150000_retire_visitor_rows.sql`'s: these are
authored fixtures, not the record of anything, and they are rewritten in full
the moment the process restarts. It runs in one transaction and asserts the
trigger came back on, because an append-only guarantee that is silently off is
worse than one that was never claimed.

Exits 0 done, 1 refused or failed, 2 nothing to do.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.core import config  # noqa: E402

#: Exit code for "there was nothing to remove", kept apart from a failure.
NOTHING = 2

#: The ids `seed.py` mints (`Call.id` → `iv_demo_{n:02d}`). `starts_with` rather
#: than LIKE because `_` is a single-character wildcard, and `iv_demo_%` would
#: also match an id nobody in this repo wrote.
SEEDED = "starts_with(i.id, 'iv_demo_')"

RED = "\033[31m"
GREEN = "\033[32m"
DIM = "\033[2m"
OFF = "\033[0m"


def _die(message: str, code: int = 1) -> None:
    print(f"{RED}{message}{OFF}", file=sys.stderr)
    raise SystemExit(code)


async def _counts(conn, where: str, *args) -> dict[str, int]:
    """What the delete is about to remove, by table."""
    return {
        "transcript.events": await conn.fetchval(
            f"select count(*) from transcript.events e "
            f"join clinical.interviews i on i.id = e.interview_id where {where}",
            *args,
        ),
        "clinical.results": await conn.fetchval(
            f"select count(*) from clinical.results r "
            f"join clinical.interviews i on i.id = r.interview_id where {where}",
            *args,
        ),
        "clinical.invitations": await conn.fetchval(
            f"select count(*) from clinical.invitations v "
            f"join clinical.interviews i on i.id = v.interview_id where {where}",
            *args,
        ),
        "clinical.interviews": await conn.fetchval(
            f"select count(*) from clinical.interviews i where {where}", *args
        ),
    }


async def _delete(conn, where: str, *args) -> None:
    """One transaction, so a refused `disable trigger` leaves nothing half-done.

    Ownership is the realistic failure: on a pooled Supabase connection the role
    may not own `transcript.events`, and the alter is refused. Rolling back is
    the difference between "try the SQL editor instead" and a database whose
    transcripts are gone and whose interviews are not.
    """
    async with conn.transaction():
        await conn.execute("alter table transcript.events disable trigger events_immutable")
        try:
            await conn.execute(
                f"delete from transcript.events e using clinical.interviews i "
                f"where i.id = e.interview_id and {where}",
                *args,
            )
        finally:
            await conn.execute("alter table transcript.events enable trigger events_immutable")

        for table, alias, key in (
            ("clinical.results", "r", "interview_id"),
            ("clinical.invitations", "v", "interview_id"),
        ):
            await conn.execute(
                f"delete from {table} {alias} using clinical.interviews i "
                f"where i.id = {alias}.{key} and {where}",
                *args,
            )
        await conn.execute(f"delete from clinical.interviews i where {where}", *args)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", metavar="ID", help="one seeded interview, e.g. iv_demo_07")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    if not config.DATABASE_URL:
        _die("DATABASE_URL is empty — this deployment keeps no Postgres record to reseed.", NOTHING)

    where, params = SEEDED, ()
    if args.only:
        if not args.only.startswith("iv_demo_"):
            _die(f"{args.only} is not a seeded id — this only removes calls seed.py wrote.")
        where, params = f"{SEEDED} and i.id = $1", (args.only,)

    import asyncpg

    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        before = await _counts(conn, where, *params)
        if not before["clinical.interviews"]:
            print(f"{DIM}nothing to reseed — no seeded calls matched{OFF}")
            raise SystemExit(NOTHING)

        host = urlparse(config.DATABASE_URL).hostname
        print(f"\n  {DIM}database{OFF} {host}")
        print(f"  {DIM}removing{OFF} {args.only or 'every seeded call (iv_demo_*)'}\n")
        for table, n in before.items():
            print(f"    {table:22} {n}")

        if not args.yes:
            print(f"\n  {DIM}real calls are untouched. the next boot writes these back.{OFF}")
            if input("  type 'yes' to continue: ").strip().lower() != "yes":
                _die("\n  cancelled — nothing was deleted.")

        await _delete(conn, where, *params)

        # `::text` because `tgenabled` is Postgres's internal `"char"`, which
        # asyncpg decodes to bytes -- so a bare read compares `b'O'` against
        # `'O'` and reports an append-only failure that did not happen.
        enabled = await conn.fetchval(
            "select tgenabled::text from pg_trigger where tgname = 'events_immutable'"
        )
        if enabled != "O":
            _die(
                f"\n  events_immutable is {enabled!r}, not 'O' — "
                "append-only is OFF. Fix before use."
            )

        after = await _counts(conn, where, *params)
        left = sum(after.values())
        gone = "  ".join(f"{table.split('.')[1]} {n}" for table, n in before.items())
        print(f"\n  {GREEN}removed{OFF}  {gone}")
        print(f"  {DIM}events_immutable back on · {left} rows left matching{OFF}")
        print("\n  Restart the backend — seed.py writes them again on boot.\n")
    finally:
        await conn.close()


asyncio.run(main())
