"""SSE transport — the mechanics that never change from one push endpoint to
the next: the heartbeat, disconnect detection, shutdown draining. What DOES
change per endpoint — whether a given subscriber may see a given item — is
never this module's business, and never optional: `authorize` is a required
keyword, run on every item with no exceptions, so a route that wires this up
cannot forget the one check that matters. See `shared/auth/deps.py`'s own
rule: no authorisation decision lives anywhere but beside the query it gates.
"""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import Request

HEARTBEAT_S = 15.0


async def stream_events[T](
    request: Request,
    queue: asyncio.Queue[T],
    shutdown: object,
    *,
    authorize: Callable[[T], Awaitable[bool]],
    heartbeat_s: float = HEARTBEAT_S,
):
    """SSE body for one subscriber's queue. The caller owns subscribing and
    unsubscribing — this only consumes what's already in the queue."""
    while True:
        if await request.is_disconnected():
            return
        try:
            item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
        except TimeoutError:
            yield ": keep-alive\n\n"
            continue
        if item is shutdown:
            return
        if await authorize(item):
            yield f"data: {item}\n\n"
