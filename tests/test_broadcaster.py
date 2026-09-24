"""The broadcaster's fan-out mechanics, on their own — no database, no HTTP."""

import asyncio

import pytest

from services.core.broadcaster import QUEUE_SIZE, SHUTDOWN, Broadcaster


@pytest.fixture
def broadcaster() -> Broadcaster:
    """A fresh instance per test — the point of `Broadcaster` being a class
    rather than module state is that nothing needs clearing between tests."""
    return Broadcaster()


async def test_a_publish_reaches_every_open_subscriber(broadcaster):
    a = broadcaster.subscribe()
    b = broadcaster.subscribe()

    broadcaster.publish("iv_1")

    assert a.get_nowait() == "iv_1"
    assert b.get_nowait() == "iv_1"


async def test_unsubscribing_stops_further_nudges(broadcaster):
    a = broadcaster.subscribe()
    broadcaster.unsubscribe(a)

    broadcaster.publish("iv_1")

    assert a.empty()


async def test_a_slow_subscriber_is_dropped_not_awaited(broadcaster):
    slow = broadcaster.subscribe()
    for _ in range(QUEUE_SIZE):
        slow.put_nowait("filler")

    broadcaster.publish("iv_overflow")  # must not raise, must not block

    assert slow.qsize() == QUEUE_SIZE
    assert all(slow.get_nowait() == "filler" for _ in range(QUEUE_SIZE))


async def test_a_full_subscriber_does_not_stop_the_others(broadcaster):
    full = broadcaster.subscribe()
    for _ in range(QUEUE_SIZE):
        full.put_nowait("filler")
    fine = broadcaster.subscribe()

    broadcaster.publish("iv_2")

    assert fine.get_nowait() == "iv_2"


async def test_shutdown_wakes_every_open_stream(broadcaster):
    a = broadcaster.subscribe()
    b = broadcaster.subscribe()

    await broadcaster.close()

    assert a.get_nowait() is SHUTDOWN
    assert b.get_nowait() is SHUTDOWN


async def test_shutdown_does_not_raise_on_a_full_queue(broadcaster):
    full = broadcaster.subscribe()
    for _ in range(QUEUE_SIZE):
        full.put_nowait("filler")

    await broadcaster.close()  # must not raise


async def test_a_waiting_getter_wakes_immediately_on_shutdown(broadcaster):
    queue = broadcaster.subscribe()

    async def waiter():
        return await asyncio.wait_for(queue.get(), timeout=5)

    task = asyncio.ensure_future(waiter())
    await asyncio.sleep(0)  # let the waiter actually start waiting
    await broadcaster.close()

    assert await task is SHUTDOWN


async def test_two_broadcasters_do_not_see_each_other():
    """The reason `Broadcaster` is instantiable: a second live-push topic gets
    its own instance and its subscribers never see the first topic's events."""
    escalations = Broadcaster()
    other = Broadcaster()

    a = escalations.subscribe()
    b = other.subscribe()

    escalations.publish("iv_1")

    assert a.get_nowait() == "iv_1"
    assert b.empty()
