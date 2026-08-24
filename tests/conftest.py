"""Shared fixtures.

Three jobs. Keep session logs out of the repo's own `logs/` directory — a test
that creates a session would otherwise leave a real JSONL file behind — and
reset the module-level state that `services/core` deliberately holds in-process,
so tests cannot leak sessions, tasks or spent rate-limit tokens into each other.
And supply the Postgres-backed tests a database, without costing the pure suite
its "no key, no LiveKit, no database" property: `db` skips unless
TEST_DATABASE_URL is set, and asyncpg is imported inside the fixtures rather
than here, so an ordinary `make test` never opens a connection.
"""

import asyncio
import os
from pathlib import Path

import pytest

from services.agent.session_log import JsonlSessionWriter


@pytest.fixture(autouse=True)
def logs_in_tmp(monkeypatch, tmp_path: Path):
    """Every session written by a test lands under `tmp_path`, not `logs/`."""
    from services.core import store

    monkeypatch.setattr(
        store,
        "JsonlSessionWriter",
        lambda session_id: JsonlSessionWriter(session_id, log_dir=tmp_path / "logs"),
    )
    return tmp_path / "logs"


@pytest.fixture(autouse=True)
async def fresh_state():
    """The backend is stateful by design (it holds live WebRTC connections), so
    the state is module-level and has to be swept between tests rather than
    constructed per test.
    """
    from services.core import app as app_module
    from services.core import store

    yield

    for task in [*app_module._tasks.values(), *app_module._watchdogs.values()]:
        task.cancel()
    # Let the cancellations actually land before the next test starts a call.
    await asyncio.gather(
        *[*app_module._tasks.values(), *app_module._watchdogs.values()],
        return_exceptions=True,
    )
    app_module._tasks.clear()
    app_module._watchdogs.clear()
    app_module._starts._buckets.clear()
    store._sessions.clear()


MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


def migration_sql() -> str:
    """Every migration in filename order, which is how Supabase applies them."""
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, f"no migrations under {MIGRATIONS}"
    return "\n".join(f.read_text() for f in files)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is unset — run `make test-pg`")
    return url


@pytest.fixture(scope="session")
async def schema(database_url):
    """Apply the migrations once, into a scratch database of our own.

    A separate database rather than the connection's default: the target may be
    a real project, and these tests insert rows the append-only triggers would
    then refuse to let anyone delete.
    """
    import asyncpg

    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute("drop database if exists metafora_test with (force)")
        await admin.execute("create database metafora_test")
    finally:
        await admin.close()

    scratch = database_url.rsplit("/", 1)[0] + "/metafora_test"
    conn = await asyncpg.connect(scratch)
    try:
        await conn.execute(migration_sql())
    finally:
        await conn.close()
    yield scratch


@pytest.fixture
async def db(schema):
    """A connection whose every write is rolled back when the test ends.

    Rollback rather than delete-after: `transcript.events` and
    `config.protocols` refuse DELETE by trigger, so a test that inserted into
    either could not clean up after itself any other way.
    """
    import asyncpg

    conn = await asyncpg.connect(schema)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()
