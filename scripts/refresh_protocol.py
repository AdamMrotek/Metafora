"""Bring a stored `config.protocols` row back into line with the code.

    uv run python scripts/refresh_protocol.py                 # every drifted row
    uv run python scripts/refresh_protocol.py proto_x_v2      # named rows only
    uv run python scripts/refresh_protocol.py --check         # report, change nothing
    uv run python scripts/refresh_protocol.py --yes           # no confirmation

`db.seed_protocols()` inserts `on conflict do nothing`, so a version seeded once
is never corrected by a later boot — and `protocols_immutable` refuses UPDATE,
so it cannot be corrected by hand either. That is exactly right for a published
version and exactly wrong for one still being authored, because the two copies
are read by different halves of the product:

    the call     compiles the *code* object — the machine, the tool schema,
                 the gate and the question flags all come from there;
    the record    resolves every filed hit against the *stored* row — the
                 escalation band's label, its action, its deadline.

Drift between them is silent and asymmetric. A call raises a flag the dashboard
then cannot name, so the band stays empty and the only symptom is an escalation
that never appears.

**It refuses anything but an addition.** A leaf that changed value or vanished
would move the meaning of something already filed — the thing publishing a new
id exists to prevent — so this stops and says so rather than papering over it.
Additions cannot: a question flag that was not there raised nothing, and a
section that was not there was still asked, because the call compiled the code.

**It suspends `protocols_immutable`**, which is the one thing here that wants
saying out loud. Same justification as `reseed.py`'s: one transaction, the
trigger restored in a `finally`, and the state asserted afterwards, because an
append-only guarantee that is silently off is worse than one never claimed.

Exits 0 done, 1 refused or failed, 2 nothing to do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.agent.config.protocol import PROTOCOLS  # noqa: E402
from services.core import config  # noqa: E402

#: Exit code for "nothing had drifted", kept apart from a failure.
NOTHING = 2

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
DIM = "\033[2m"
OFF = "\033[0m"


def _die(message: str, code: int = 1) -> None:
    print(f"{RED}{message}{OFF}", file=sys.stderr)
    raise SystemExit(code)


def leaves(node, path: str = ""):
    """Every scalar in the document, by path.

    Compared leaf by leaf rather than blob to blob because *what* differs is the
    whole question: an addition is safe to apply and a change is not.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaves(value, f"{path}[{i}]")
    else:
        yield path, node


def as_stored(protocol) -> dict:
    """The code's version, in the shape `seed_protocols` would have written."""
    return json.loads(json.dumps(protocol.model_dump(by_alias=True), default=str))


def compare(stored: dict, code: dict) -> tuple[list[str], list[str], list[str]]:
    was, now = dict(leaves(stored)), dict(leaves(code))
    added = [k for k in now if k not in was]
    removed = [k for k in was if k not in now]
    changed = [k for k in was if k in now and was[k] != now[k]]
    return added, removed, changed


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", metavar="ID", help="protocol ids; default all")
    parser.add_argument("--check", action="store_true", help="report drift, change nothing")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    if not config.DATABASE_URL:
        _die("DATABASE_URL is empty — this deployment keeps no Postgres record.", NOTHING)

    wanted = args.ids or list(PROTOCOLS)
    unknown = [i for i in wanted if i not in PROTOCOLS]
    if unknown:
        _die(f"not protocols this code publishes: {', '.join(unknown)}")

    import asyncpg

    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        stored = {
            r["id"]: json.loads(r["version"])
            for r in await conn.fetch("select id, version from config.protocols")
        }

        host = urlparse(config.DATABASE_URL).hostname
        print(f"\n  {DIM}database{OFF} {host}\n")

        plan: list[tuple[str, dict, int]] = []
        refused: list[str] = []

        for pid in wanted:
            code = as_stored(PROTOCOLS[pid])
            if pid not in stored:
                print(f"    {DIM}{pid:<24} not seeded — the next boot inserts it{OFF}")
                continue

            added, removed, changed = compare(stored[pid], code)
            if removed or changed:
                refused.append(pid)
                print(f"    {RED}{pid:<24} {len(changed)} changed, {len(removed)} removed{OFF}")
                for key in (changed + removed)[:5]:
                    print(f"      {DIM}{key}{OFF}")
                continue
            if not added:
                print(f"    {DIM}{pid:<24} current{OFF}")
                continue

            plan.append((pid, code, len(added)))
            print(f"    {YELLOW}{pid:<24} +{len(added)} leaves{OFF}")

        if refused:
            _die(
                "\n  Refused. A leaf that changed or vanished moves the meaning of\n"
                "  something already filed — publish a new id rather than editing one.\n"
                f"  {', '.join(refused)}"
            )

        if not plan:
            print(f"\n  {DIM}nothing had drifted{OFF}\n")
            raise SystemExit(NOTHING)

        if args.check:
            print(f"\n  {DIM}--check: nothing was written{OFF}\n")
            return

        if not args.yes:
            print(f"\n  {DIM}additions only. protocols_immutable is suspended for one"
                  f" transaction.{OFF}")
            if input("  type 'yes' to continue: ").strip().lower() != "yes":
                _die("\n  cancelled — nothing was written.")

        async with conn.transaction():
            await conn.execute(
                "alter table config.protocols disable trigger protocols_immutable"
            )
            try:
                for pid, code, _ in plan:
                    await conn.execute(
                        "update config.protocols set version = $2::jsonb where id = $1",
                        pid,
                        json.dumps(code),
                    )
            finally:
                await conn.execute(
                    "alter table config.protocols enable trigger protocols_immutable"
                )

        # `::text` because `tgenabled` is Postgres's internal `"char"`, which
        # asyncpg decodes to bytes — a bare read compares b'O' against 'O' and
        # reports an append-only failure that did not happen.
        enabled = await conn.fetchval(
            "select tgenabled::text from pg_trigger where tgname = 'protocols_immutable'"
        )
        if enabled != "O":
            _die(
                f"\n  protocols_immutable is {enabled!r}, not 'O' — "
                "append-only is OFF. Fix before use."
            )

        after = {
            r["id"]: json.loads(r["version"])
            for r in await conn.fetch("select id, version from config.protocols")
        }
        for pid, code, _ in plan:
            if after[pid] != code:
                _die(f"\n  {pid} did not land — the row still differs from the code.")

        names = "  ".join(pid for pid, _, _ in plan)
        print(f"\n  {GREEN}refreshed{OFF}  {names}")
        print(f"  {DIM}protocols_immutable back on · read back and verified{OFF}\n")
    finally:
        await conn.close()


asyncio.run(main())
