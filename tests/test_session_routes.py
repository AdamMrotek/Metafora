"""The patient's routes — `services/core/routes/session.py`.

The unauthenticated surface: what a browser may ask for, what it is told, and
what it is refused. The call these routes start is `test_lifecycle.py`'s
subject; what is held here is the door. `tests/fakes.py` explains how this runs
with no SFU, no Groq key and no network. What is being held:

* what `POST /session` hands back, which is what `frontend/call` was written
  against — a URL a browser can actually resolve, and a machine it can address
* the four refusals standing between an unauthenticated route and an unbounded
  bill, and that each of them leaves nothing behind
* typed answers, which must arrive the way spoken ones do
* the failure body shape the portal reads
"""

from services.core import lifecycle, store
from services.core.limits import DailyQuota, RateLimiter
from services.core.routes import session as session_routes
from services.core.routes.session import BUSY_MESSAGE, TYPED_USER_ID
from tests.asgi import client, start

# ─── health ──────────────────────────────────────────────────────────────────


async def test_health_reports_capacity_not_just_liveness():
    async with client() as http:
        body = (await http.get("/health")).json()
    assert body["ok"] is True
    assert body["live"] == 0
    # An operator at 3am needs to know how close to full it is, not only that
    # it answered.
    assert body["capacity"] == session_routes.MAX_CONCURRENT_SESSIONS


# ─── what the browser is handed ──────────────────────────────────────────────


async def test_session_hands_the_browser_the_public_url(bots, monkeypatch):
    """The backend may dial the SFU on an address no browser can resolve."""
    monkeypatch.setattr(session_routes, "LIVEKIT_PUBLIC_URL", "wss://public.example")
    monkeypatch.setattr(lifecycle, "LIVEKIT_URL", "ws://internal:7880")

    async with client() as http:
        body = (await start(http)).json()

    assert body["url"] == "wss://public.example"
    assert body["token"]
    assert body["session"]["patientFirstName"]


# ─── typed answers ───────────────────────────────────────────────────────────


async def test_typed_answer_enters_as_a_transcription_not_a_context_append(bots):
    """A typed answer must reach the model *through* the safety gate, and the
    gate only ever sees transcripts. Appending straight to the context would let
    a patient type the one thing the gate exists to catch.
    """
    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        response = await http.post(f"/session/{session_id}/typed", json={"text": "my knee hurts"})

    assert response.status_code == 200
    frame = bots.latest.worker.frames[-1]
    assert type(frame).__name__ == "TranscriptionFrame"
    assert frame.text == "my knee hurts"
    assert frame.user_id == TYPED_USER_ID


async def test_blank_typed_answer_queues_nothing(bots):
    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        before = len(bots.latest.worker.frames)
        await http.post(f"/session/{session_id}/typed", json={"text": "   "})

    assert len(bots.latest.worker.frames) == before


async def test_typed_to_an_unknown_session_is_404(bots):
    async with client() as http:
        response = await http.post("/session/s_nope/typed", json={"text": "hello"})
    assert response.status_code == 404


# ─── the caps ────────────────────────────────────────────────────────────────


async def test_at_capacity_the_next_caller_is_refused(bots, monkeypatch):
    monkeypatch.setattr(session_routes, "MAX_CONCURRENT_SESSIONS", 1)

    async with client() as http:
        assert (await start(http)).status_code == 200
        refused = await start(http)

    assert refused.status_code == 503
    assert refused.json()["error"] == BUSY_MESSAGE
    assert refused.headers["retry-after"]
    # A refused caller leaves nothing behind: no record, no room, no log file.
    assert len(store.live_sessions()) == 1


async def test_rate_limit_refuses_a_loop_but_not_a_second_patient(bots, monkeypatch):
    monkeypatch.setattr(session_routes, "_starts", RateLimiter(burst=1, window_s=300))

    async with client(ip="198.51.100.1") as looper:
        assert (await start(looper)).status_code == 200
        refused = await start(looper)
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0

    # Someone else's browser is not the loop, and must not pay for it.
    async with client(ip="198.51.100.2") as other:
        assert (await start(other)).status_code == 200


async def test_the_daily_quota_refuses_what_the_per_ip_limiter_cannot(bots, monkeypatch):
    """Per-IP is defeated by having more addresses. This is the cap that is not.

    Two different callers, one quota: the second is refused even though its own
    bucket is full, which is the whole point of the thing.
    """
    monkeypatch.setattr(session_routes, "_today", DailyQuota(limit=1))

    async with client(ip="198.51.100.10") as first:
        assert (await start(first)).status_code == 200

    async with client(ip="198.51.100.11") as second:
        refused = await start(second)

    assert refused.status_code == 503
    assert refused.json()["error"] == BUSY_MESSAGE
    assert len(store.live_sessions()) == 1


async def test_the_off_switch_refuses_before_anything_exists(bots, monkeypatch):
    """Closing a public demo has to be a secret change, not a redeploy — a
    redeploy is the thing you cannot do calmly at the moment you need this.
    """
    monkeypatch.setattr(session_routes, "ACCEPTING_SESSIONS", False)

    async with client() as http:
        refused = await start(http)

    assert refused.status_code == 503
    assert refused.json()["error"] == BUSY_MESSAGE
    assert store.live_sessions() == []


# ─── staying reachable ───────────────────────────────────────────────────────


async def test_the_bootstrap_carries_the_machine_that_holds_the_call(bots, monkeypatch):
    """`/session/{id}/typed` has to reach the process that holds the session,
    because the session is a dict entry in that process's memory. On a second
    machine, without this, typed answers 404 intermittently and it reads as a
    bug in the pipeline rather than as routing.

    Nothing depends on it while one machine is deployed. It is tested so that
    scaling to two is `fly scale count` and not an afternoon.
    """
    monkeypatch.setattr(session_routes, "FLY_MACHINE_ID", "148ed193b16d89")

    async with client() as http:
        body = (await start(http)).json()

    assert body["session"]["machineId"] == "148ed193b16d89"


async def test_off_fly_there_is_no_machine_to_name(bots, monkeypatch):
    """Absent, not empty: the browser spreads the header in only when it has a
    value, and `""` would be a header that routes nowhere."""
    monkeypatch.setattr(session_routes, "FLY_MACHINE_ID", "")

    async with client() as http:
        body = (await start(http)).json()

    assert body["session"]["machineId"] is None


# ─── the failure body ────────────────────────────────────────────────────────


async def test_failures_speak_the_body_shape_the_portal_reads(bots):
    """`useCall.ts` reads `.error`; FastAPI's default is `.detail`. Without the
    handler every failure reached the patient as a generic "could not start".
    """
    async with client() as http:
        body = (await http.post("/session/s_nope/typed", json={"text": "x"})).json()

    assert "error" in body
    assert "detail" not in body
