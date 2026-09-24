"""In-process fan-out for a live-push topic. One machine only: this module
holds state a second machine could not see, which is why `fly.toml` pins
`min_machines_running = 1`.

`Broadcaster` is the reusable shape. `escalations` below is the one topic
that exists today (5b·2) — a second live-push feature instantiates its own
`Broadcaster()` rather than sharing this one, so its events never need
filtering against a subscriber that never asked for them.
"""

import asyncio
from typing import Final

from loguru import logger

#: A call must never feel a dashboard — a full queue means a subscriber's
#: browser stopped reading, and its nudge is dropped rather than awaited.
QUEUE_SIZE: Final = 8

#: Wakes a stream blocked on `queue.get()` so shutdown ends it cleanly.
#: Shared across every `Broadcaster` — it's an identity check, not a value.
SHUTDOWN: Final = object()


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, item) -> None:
        """Fan out to every open stream. Must never block — see `QUEUE_SIZE`."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                logger.warning(f"[broadcaster] a subscriber is behind — dropping {item}")

    async def close(self) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(SHUTDOWN)
            except asyncio.QueueFull:
                pass


#: A red flag raised on any call, fanned out to every open dashboard and
#: filtered back down to `OWNED_BY` per subscriber in `routes/interviews.py`'s
#: stream route — never here, because nothing upstream of that route knows
#: whose caseload a call is in.
escalations = Broadcaster()
