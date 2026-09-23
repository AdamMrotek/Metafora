"""In-process fan-out for the live-push nudge — 5b·2. One machine only:
this module holds state a second machine could not see, which is why
`fly.toml` pins `min_machines_running = 1`.
"""

import asyncio
from typing import Final

from loguru import logger

#: A call must never feel a dashboard — a full queue means a subscriber's
#: browser stopped reading, and its nudge is dropped rather than awaited.
QUEUE_SIZE: Final = 8

#: Wakes a stream blocked on `queue.get()` so shutdown ends it cleanly.
SHUTDOWN: Final = object()

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.discard(queue)


def publish(interview_id: str) -> None:
    """Fan out to every open stream. Must never block — see `QUEUE_SIZE`."""
    for queue in list(_subscribers):
        try:
            queue.put_nowait(interview_id)
        except asyncio.QueueFull:
            logger.warning(f"[broadcaster] a subscriber is behind — dropping {interview_id}")


async def close() -> None:
    for queue in list(_subscribers):
        try:
            queue.put_nowait(SHUTDOWN)
        except asyncio.QueueFull:
            pass
