# SSE orchestration — where it's wired up

Two call sites, one on each side. Everything else — `services/core/sse.py`,
`services/core/broadcaster.py`, and the reconnect/watchdog logic inside
`frontend/dashboard/src/stream.ts` — is machinery these two compose. Nothing
else in the app should import `sse.py` or `broadcaster.py` directly.

## Backend — `services/core/routes/interviews.py`

```python
from services.core import broadcaster, reads, sse
from shared.auth import ClinicalReader

@router.get("/interviews/stream")
async def stream_interviews(user: ClinicalReader, request: Request) -> StreamingResponse:
    return StreamingResponse(_stream_events(user, request), media_type="text/event-stream")

async def _stream_events(user: ClinicalReader, request: Request):
    queue = broadcaster.escalations.subscribe()
    try:
        async for chunk in sse.stream_events(
            request,
            queue,
            broadcaster.SHUTDOWN,
            authorize=lambda interview_id: reads.in_scope(user, interview_id),
        ):
            yield chunk
    finally:
        broadcaster.escalations.unsubscribe(queue)
```

`ClinicalReader` is the door. `authorize=` is the row check — a required
keyword with no default, supplied here and nowhere else. `sse.stream_events`
is the only thing that knows about heartbeats, disconnects and shutdown.

## Frontend — `frontend/dashboard/src/data.tsx`

```ts
import { useEscalationStream } from './stream.ts';

export function RecordProvider({ children }: { children: ReactNode }) {
  const [nonce, setNonce] = useState(0);
  // ...fetch effect keyed on [nonce]...

  useEscalationStream(() => setNonce((n) => n + 1));

  // ...
}
```

One line. `useEscalationStream` owns the subscription, the backoff reconnect,
the stale-connection watchdog and the 30s backstop poll — the caller only
says what a nudge means.

## Adding a second stream

A new SSE feature gets its own `Broadcaster()` instance — never shares
`escalations` — and supplies its own `authorize` at its own route.
`sse.py` on the backend and the transport half of `stream.ts` on the
frontend are the only parts meant to be reused as-is.
