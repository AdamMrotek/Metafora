"""Shared fixtures.

Six jobs. Keep session logs out of the repo's own `logs/` directory — a test
that creates a session would otherwise leave a real JSONL file behind — reset
the module-level state that `services/core` deliberately holds in-process, so
tests cannot leak sessions, tasks or spent rate-limit tokens into each other,
clear the authenticator `shared/auth` holds in another one, hold the pure
suite's "no key, no LiveKit, no database" property against a developer `.env`
that names a real project, and stand in for the bot on behalf of the two suites
that drive a real call — `test_session_routes.py` and `test_lifecycle.py`.

That last one is `no_database`, and it is not a formality. `config.py` reads
`.env` at import and this repo's own `.env` has a populated `DATABASE_URL`, so
without it a plain `make test` would open a pool against the hosted Supabase and
write rows that the append-only triggers then refuse to let anyone delete.

And supply the Postgres-backed tests a database: `db` and `pool` skip unless
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
def no_database(monkeypatch):
    """The pure suite reaches no database, whatever `.env` says.

    `db.enabled()` is `_pool is not None`, so clearing the pool is enough for
    every caller; `connect` is stubbed as well because `TestClient` runs
    `lifespan`, which would otherwise dial out before a single test body ran.
    """
    from services.core import db

    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "connect", _never_connect)


async def _never_connect() -> None:
    raise AssertionError("the pure suite must not open a database connection")


@pytest.fixture(autouse=True)
def no_authenticator():
    """No test inherits the door another test installed.

    `shared/auth` holds the authenticator in a module global, exactly as
    `services/core` holds live sessions in one, and for the same reason: there
    is one per process. Clearing it before every test means the default posture
    under test is the deployed one — unconfigured refuses, it does not admit.
    """
    from shared import auth

    auth.configure(None)
    yield
    auth.configure(None)


@pytest.fixture(autouse=True)
async def fresh_state():
    """The backend is stateful by design (it holds live WebRTC connections), so
    the state is module-level and has to be swept between tests rather than
    constructed per test.
    """
    from services.core import lifecycle, store
    from services.core.routes import session as session_routes

    yield

    pending = [
        *lifecycle._tasks.values(),
        *lifecycle._watchdogs.values(),
        *lifecycle._arrivals.values(),
    ]
    for task in pending:
        task.cancel()
    # Let the cancellations actually land before the next test starts a call.
    await asyncio.gather(*pending, return_exceptions=True)
    lifecycle._tasks.clear()
    lifecycle._watchdogs.clear()
    lifecycle._arrivals.clear()
    session_routes._starts._buckets.clear()
    store._sessions.clear()



@pytest.fixture
def bots(monkeypatch):
    """Both halves of the call live in `lifecycle` — the bot it builds and the
    runner it drives — so one module is still the whole substitution."""
    from services.core import lifecycle
    from tests import fakes

    return fakes.install(monkeypatch, lifecycle)


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
async def pool(schema):
    """A real asyncpg pool, for the code that takes one rather than a connection.

    No rollback here — a pool hands out a different connection per call, so a
    wrapping transaction would not contain the writes. Tests scope their
    assertions to ids they generated instead, which the append-only triggers
    force anyway: rows in `transcript.events` cannot be deleted afterwards.
    """
    import asyncpg

    from services.core import db as db_module

    p = await asyncpg.create_pool(schema, min_size=1, max_size=4, init=db_module._codecs)
    try:
        yield p
    finally:
        await p.close()


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
