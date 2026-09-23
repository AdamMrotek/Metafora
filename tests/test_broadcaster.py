"""The broadcaster's fan-out mechanics, on their own — no database, no HTTP."""

import asyncio

import pytest

from services.core import broadcaster


@pytest.fixture(autouse=True)
def _no_leaked_subscribers():
    broadcaster._subscribers.clear()
    yield
    broadcaster._subscribers.clear()


async def test_a_publish_reaches_every_open_subscriber():
    a = broadcaster.subscribe()
    b = broadcaster.subscribe()

    broadcaster.publish("iv_1")

    assert a.get_nowait() == "iv_1"
    assert b.get_nowait() == "iv_1"


async def test_unsubscribing_stops_further_nudges():
    a = broadcaster.subscribe()
    broadcaster.unsubscribe(a)

    broadcaster.publish("iv_1")

    assert a.empty()


async def test_a_slow_subscriber_is_dropped_not_awaited():
    slow = broadcaster.subscribe()
    for _ in range(broadcaster.QUEUE_SIZE):
        slow.put_nowait("filler")

    broadcaster.publish("iv_overflow")  # must not raise, must not block

    assert slow.qsize() == broadcaster.QUEUE_SIZE
    assert all(slow.get_nowait() == "filler" for _ in range(broadcaster.QUEUE_SIZE))


async def test_a_full_subscriber_does_not_stop_the_others():
    full = broadcaster.subscribe()
    for _ in range(broadcaster.QUEUE_SIZE):
        full.put_nowait("filler")
    fine = broadcaster.subscribe()

    broadcaster.publish("iv_2")

    assert fine.get_nowait() == "iv_2"


async def test_shutdown_wakes_every_open_stream():
    a = broadcaster.subscribe()
    b = broadcaster.subscribe()

    await broadcaster.close()

    assert a.get_nowait() is broadcaster.SHUTDOWN
    assert b.get_nowait() is broadcaster.SHUTDOWN


async def test_shutdown_does_not_raise_on_a_full_queue():
    full = broadcaster.subscribe()
    for _ in range(broadcaster.QUEUE_SIZE):
        full.put_nowait("filler")

    await broadcaster.close()  # must not raise


async def test_a_waiting_getter_wakes_immediately_on_shutdown():
    queue = broadcaster.subscribe()

    async def waiter():
        return await asyncio.wait_for(queue.get(), timeout=5)

    task = asyncio.ensure_future(waiter())
    await asyncio.sleep(0)  # let the waiter actually start waiting
    await broadcaster.close()

    assert await task is broadcaster.SHUTDOWN
