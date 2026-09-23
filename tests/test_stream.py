"""The live-push stream against a real Postgres — that it shows a nudge only
to the caller who owns the interview. Deselected by default; `make test-pg`
supplies TEST_DATABASE_URL."""

import asyncio

import pytest

from services.core import broadcaster, db, dispatch, reads
from services.core.routes.interviews import _stream_events
from shared.auth import CurrentUser
from shared.contracts.models import DispatchRequest

pytestmark = pytest.mark.postgres

ALICE = "alice@example.test"
BOB = "bob@example.test"


def user(email: str = ALICE) -> CurrentUser:
    return CurrentUser(email=email, sub="auth-user", role="clinician", display_name="Test")


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


@pytest.fixture
async def live_db(pool):
    db.configure(pool)
    await db.seed_protocols()
    await pool.executemany(
        "insert into config.accounts (email, role, display_name) values ($1, 'clinician', $2) "
        "on conflict (email) do nothing",
        [(ALICE, "Dr Alice"), (BOB, "Dr Bob")],
    )
    broadcaster._subscribers.clear()
    try:
        yield pool
    finally:
        broadcaster._subscribers.clear()
        db.configure(None)


async def _next(agen):
    """Yield control so the generator has subscribed and parked on
    `queue.get()` before the caller publishes into it."""
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0)
    return task


async def test_in_scope_is_true_for_the_owner_and_false_for_a_stranger(live_db):
    summary = await dispatch.create_interview(
        user(ALICE), DispatchRequest(first_name="Nadia", protocol_id="proto_warmup_v1")
    )

    assert await reads.in_scope(user(ALICE), summary.id) is True
    assert await reads.in_scope(user(BOB), summary.id) is False
    assert await reads.in_scope(user(ALICE), "iv_does_not_exist") is False


async def test_the_stream_delivers_to_the_owner(live_db):
    summary = await dispatch.create_interview(
        user(ALICE), DispatchRequest(first_name="Priya", protocol_id="proto_warmup_v1")
    )

    agen = _stream_events(user(ALICE), FakeRequest())
    try:
        task = await _next(agen)
        broadcaster.publish(summary.id)
        event = await asyncio.wait_for(task, timeout=1)
        assert event == f"data: {summary.id}\n\n"
    finally:
        await agen.aclose()


async def test_the_stream_withholds_a_strangers_interview(live_db):
    summary = await dispatch.create_interview(
        user(ALICE), DispatchRequest(first_name="Rosa", protocol_id="proto_warmup_v1")
    )

    agen = _stream_events(user(BOB), FakeRequest())
    try:
        task = await _next(agen)
        broadcaster.publish(summary.id)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=0.2)
    finally:
        await agen.aclose()


async def test_shutdown_ends_the_stream(live_db):
    agen = _stream_events(user(ALICE), FakeRequest())
    task = await _next(agen)
    await broadcaster.close()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(task, timeout=1)
