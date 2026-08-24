"""The HTTP surface.

Everything here runs with no SFU, no Groq key and no network — `tests/fakes.py`
explains how. What is being held:

* the ordering guarantee, which is the only thing about `POST /session` that
  matters — the assistant is *in the room* before the patient holds a token
* the two caps standing between an unauthenticated route and an unbounded bill
* teardown, which has four callers and has to be idempotent for all of them
* the failure body shape `frontend/call` was written against
"""

import asyncio

import httpx
import pytest

from services.core import app as app_module
from services.core import store
from services.core.app import BUSY_MESSAGE, TYPED_USER_ID, _wire_reason, app, drain
from services.core.limits import RateLimiter
from tests import fakes


def client(ip: str = "203.0.113.7") -> httpx.AsyncClient:
    """An ASGI client with a stable caller identity, so the per-IP limiter can
    be exercised without inventing a network.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 4242)),
        base_url="http://testserver",
    )


@pytest.fixture
def bots(monkeypatch):
    return fakes.install(monkeypatch, app_module)


async def start(http: httpx.AsyncClient) -> httpx.Response:
    return await http.post("/session")


# ─── health ──────────────────────────────────────────────────────────────────


async def test_health_reports_capacity_not_just_liveness():
    async with client() as http:
        body = (await http.get("/health")).json()
    assert body["ok"] is True
    assert body["live"] == 0
    # An operator at 3am needs to know how close to full it is, not only that
    # it answered.
    assert body["capacity"] == app_module.MAX_CONCURRENT_SESSIONS


# ─── the ordering guarantee ──────────────────────────────────────────────────


async def test_patient_token_is_minted_only_after_the_assistant_is_connected(bots, monkeypatch):
    """The whole point of the route. If the token were handed out first, the
    patient could join an empty room and sit there.
    """
    order: list[str] = []
    real_mint = app_module.mint_token

    def recording_mint(room: str, identity: str, **kwargs):
        order.append(f"mint:{identity.split('-')[0]}")
        return real_mint(room, identity, **kwargs)

    monkeypatch.setattr(app_module, "mint_token", recording_mint)

    transport_fire = fakes.FakeTransport.fire

    async def recording_fire(self, name, *args):
        order.append(f"fire:{name}")
        await transport_fire(self, name, *args)

    monkeypatch.setattr(fakes.FakeTransport, "fire", recording_fire)

    async with client() as http:
        assert (await start(http)).status_code == 200

    assert order == ["mint:assistant", "fire:on_connected", "mint:patient"]


async def test_session_hands_the_browser_the_public_url(bots, monkeypatch):
    """The backend may dial the SFU on an address no browser can resolve."""
    monkeypatch.setattr(app_module, "LIVEKIT_PUBLIC_URL", "wss://public.example")
    monkeypatch.setattr(app_module, "LIVEKIT_URL", "ws://internal:7880")

    async with client() as http:
        body = (await start(http)).json()

    assert body["url"] == "wss://public.example"
    assert body["token"]
    assert body["session"]["patientFirstName"]


async def test_nothing_is_spoken_until_the_patient_is_in_the_room(bots):
    """Data published to a room a patient has not joined is not replayed to them
    on arrival, so an opening spoken early is spoken to nobody.
    """
    async with client() as http:
        await start(http)
        bot = bots.latest
        assert bot.worker.frames == []

        await bot.transport.fire("on_first_participant_joined", {"identity": "patient-pt_alice"})

    assert "thinking" in [value for kind, value in bot.wire.sent if kind == "phase"]
    assert bot.worker.spoken  # the greeting plus the first question


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


# ─── teardown ────────────────────────────────────────────────────────────────


async def test_end_is_idempotent(bots):
    """Four things can end a call — the patient, the pipeline, the watchdog and
    a server drain — and they race. It has to end once.
    """
    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        assert (await http.post(f"/session/{session_id}/end")).status_code == 200
        assert (await http.post(f"/session/{session_id}/end")).status_code == 200

    assert bots.latest.wire.kinds.count("ended") == 1
    assert store.get_session(session_id).ended is True


async def test_ending_an_unknown_session_is_silent(bots):
    async with client() as http:
        assert (await http.post("/session/s_nope/end")).status_code == 200


async def test_patient_leaving_ends_the_call(bots):
    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        await bots.latest.transport.fire("on_participant_disconnected", {"identity": "p"}, None)

    session = store.get_session(session_id)
    assert session.ended is True
    assert session.ended_reason == "patient_left"


async def test_drain_ends_every_live_call(bots):
    """A restart drops every call on the box, so the goodbye is the difference
    between a patient hearing a sentence and hearing silence.
    """
    async with client() as http:
        await start(http)
        await start(http)
    assert len(store.live_sessions()) == 2

    await drain("server_shutdown")

    assert store.live_sessions() == []
    assert all(bot.wire.kinds.count("ended") == 1 for bot in bots.bots)
    assert all(bot.worker.stopped for bot in bots.bots)


@pytest.mark.parametrize(
    ("internal", "wire"),
    [
        ("complete", "complete"),
        ("ended_by_patient", "complete"),
        ("safety", "safety"),
        ("server_shutdown", "interrupted"),
        ("max_duration", "interrupted"),
        ("pipeline_finished", "error"),
    ],
)
def test_a_healthy_shutdown_is_not_reported_to_the_patient_as_an_error(internal, wire):
    """Phase 3's blue/green makes `server_shutdown` a routine event. Calling it
    an error would mean the portal telling most patients something went wrong
    when the only thing that happened is that we deployed.
    """
    assert _wire_reason(internal) == wire


async def test_teardown_waits_for_the_goodbye_instead_of_cutting_it_off(bots):
    """Deploy blocker 5, held in a test.

    `stop_when_done()` only queues an EndFrame and returns — it does not wait
    for the pipeline to finish speaking. Teardown used to cancel the runner
    immediately after calling it, so the sentence `drain()` promises the patient
    was cut off mid-word. Teardown must let the task end on its own.
    """
    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        task = app_module._tasks[session_id]
        await http.post(f"/session/{session_id}/end")

    assert task.done()
    assert not task.cancelled(), "the pipeline was killed before it finished speaking"
    assert bots.latest.worker.stopped is True
    # `_run`'s finally block ran, which is what closes the room behind the goodbye.
    assert bots.latest.transport.disconnected is True


async def test_a_pipeline_that_will_not_stop_is_cancelled_anyway(bots, monkeypatch):
    """The wait is bounded: a hung TTS call must not hold a deploy open."""
    monkeypatch.setattr(app_module, "GOODBYE_TIMEOUT_S", 0.01)

    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        bots.latest.worker.wedged = True  # ignores the EndFrame
        task = app_module._tasks[session_id]
        await http.post(f"/session/{session_id}/end")

    assert task.cancelled() or task.done()
    assert store.get_session(session_id).ended is True


async def test_call_past_the_ceiling_is_hung_up(bots, monkeypatch):
    """A tab left open holds one of very few concurrency slots."""
    monkeypatch.setattr(app_module, "MAX_CALL_SECONDS", 0)

    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        for _ in range(10):
            await asyncio.sleep(0)
            if store.get_session(session_id).ended:
                break

    session = store.get_session(session_id)
    assert session.ended is True
    assert session.ended_reason == "max_duration"


# ─── the caps ────────────────────────────────────────────────────────────────


async def test_at_capacity_the_next_caller_is_refused(bots, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_CONCURRENT_SESSIONS", 1)

    async with client() as http:
        assert (await start(http)).status_code == 200
        refused = await start(http)

    assert refused.status_code == 503
    assert refused.json()["error"] == BUSY_MESSAGE
    assert refused.headers["retry-after"]
    # A refused caller leaves nothing behind: no record, no room, no log file.
    assert len(store.live_sessions()) == 1


async def test_rate_limit_refuses_a_loop_but_not_a_second_patient(bots, monkeypatch):
    monkeypatch.setattr(app_module, "_starts", RateLimiter(burst=1, window_s=300))

    async with client(ip="198.51.100.1") as looper:
        assert (await start(looper)).status_code == 200
        refused = await start(looper)
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0

    # Someone else's browser is not the loop, and must not pay for it.
    async with client(ip="198.51.100.2") as other:
        assert (await start(other)).status_code == 200


async def test_failures_speak_the_body_shape_the_portal_reads(bots):
    """`useCall.ts` reads `.error`; FastAPI's default is `.detail`. Without the
    handler every failure reached the patient as a generic "could not start".
    """
    async with client() as http:
        body = (await http.post("/session/s_nope/typed", json={"text": "x"})).json()

    assert "error" in body
    assert "detail" not in body
