"""The one place a database connection is opened.

Everything else asks this module whether there is a database at all. That
question is answered by the *pool*, never by the environment: a test that never
ran `lifespan` cannot reach Postgres however `.env` is configured, which is what
keeps `make test` honest on a laptop whose `.env` points at the real project.

`DATABASE_URL` empty is a supported configuration, not a degraded one — `make
dev` runs on a laptop with nothing provisioned, and the JSONL writer stays the
record there. Outside dev `config.py` refuses to import without it, so the only
way to reach production with no database is to have said so deliberately.
"""

import json
from typing import Any

from loguru import logger

from services.core.config import DATABASE_URL

_pool: Any | None = None


async def _codecs(conn) -> None:
    """Hand jsonb across as dicts rather than raw strings.

    asyncpg leaves jsonb as text by default, which would make every payload a
    string that happens to look like JSON — see `tests/test_schema.py`, which
    asserts exactly that on a connection with no codec registered.
    """
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect() -> None:
    """Open the pool. A no-op when there is no database, by design."""
    global _pool
    if _pool is not None or not DATABASE_URL:
        return

    import asyncpg

    # Six connections. The box holds `MAX_CONCURRENT_SESSIONS` (3) calls by
    # construction, and Phase 2 added the dashboard's reads on top of that —
    # short queries, but they must not queue behind a transcript batch while a
    # clinician watches a spinner. Still small, because a hosted Postgres counts
    # sockets and an idle one is not free.
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=6, init=_codecs)
    logger.info("postgres · pool open")


async def close() -> None:
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


def configure(pool: Any | None) -> None:
    """Inject a pool. Tests use this; the app uses `connect()`."""
    global _pool
    _pool = pool


def pool() -> Any | None:
    return _pool


def enabled() -> bool:
    """Whether anything should be written to Postgres at all."""
    return _pool is not None


async def seed_protocols() -> None:
    """Publish the hardcoded protocols into `config.protocols`.

    `clinical.interviews.protocol_id` references this table, so nothing can be
    dispatched until it is populated. `on conflict do nothing` never fires the
    `protocols_immutable` trigger — an INSERT that does nothing is not an
    UPDATE — so re-seeding on every boot is safe and self-healing. The agent
    studio becomes the second writer at Phase 6+.
    """
    if _pool is None:
        return

    from services.agent.config.protocol import PROTOCOLS

    await _pool.executemany(
        "insert into config.protocols (id, label, version) values ($1, $2, $3) "
        "on conflict (id) do nothing",
        [(p.id, p.label, p.model_dump(by_alias=True)) for p in PROTOCOLS.values()],
    )
