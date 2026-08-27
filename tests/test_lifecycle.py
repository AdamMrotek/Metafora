"""The life of a call — `services/core/lifecycle.py`.

Everything a call does other than answer HTTP: join a room in the right order,
speak only once someone is there to hear it, and end exactly once no matter
which of the four callers gets there first. `tests/fakes.py` explains how this
runs with no SFU, no Groq key and no network. What is being held:

* the ordering guarantee — the assistant is *in the room* before the patient
  holds a token, and nothing is spoken into a room the patient has not joined
* teardown, which has four callers and has to be idempotent for all of them
* the goodbye, which a deploy must wait for but must not be held open by
* the reason a patient is shown, which is not the reason we record
"""

import asyncio

import pytest

from services.core import lifecycle, store
from services.core.lifecycle import _wire_reason, drain
from tests import fakes
from tests.asgi import client, start

# ─── the ordering guarantee ──────────────────────────────────────────────────


async def test_patient_token_is_minted_only_after_the_assistant_is_connected(bots, monkeypatch):
    """The whole point of the route. If the token were handed out first, the
    patient could join an empty room and sit there.
    """
    order: list[str] = []
    real_mint = lifecycle.mint_token

    def recording_mint(room: str, identity: str, **kwargs):
        order.append(f"mint:{identity.split('-')[0]}")
        return real_mint(room, identity, **kwargs)

    monkeypatch.setattr(lifecycle, "mint_token", recording_mint)

    transport_fire = fakes.FakeTransport.fire

    async def recording_fire(self, name, *args):
        order.append(f"fire:{name}")
        await transport_fire(self, name, *args)

    monkeypatch.setattr(fakes.FakeTransport, "fire", recording_fire)

    async with client() as http:
        assert (await start(http)).status_code == 200

    assert order == ["mint:assistant", "fire:on_connected", "mint:patient"]


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
        task = lifecycle._tasks[session_id]
        await http.post(f"/session/{session_id}/end")

    assert task.done()
    assert not task.cancelled(), "the pipeline was killed before it finished speaking"
    assert bots.latest.worker.stopped is True
    # `_run`'s finally block ran, which is what closes the room behind the goodbye.
    assert bots.latest.transport.disconnected is True


async def test_a_pipeline_that_will_not_stop_is_cancelled_anyway(bots, monkeypatch):
    """The wait is bounded: a hung TTS call must not hold a deploy open."""
    monkeypatch.setattr(lifecycle, "GOODBYE_TIMEOUT_S", 0.01)

    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        bots.latest.worker.wedged = True  # ignores the EndFrame
        task = lifecycle._tasks[session_id]
        await http.post(f"/session/{session_id}/end")

    assert task.cancelled() or task.done()
    assert store.get_session(session_id).ended is True


async def test_call_past_the_ceiling_is_hung_up(bots, monkeypatch):
    """A tab left open holds one of very few concurrency slots."""
    monkeypatch.setattr(lifecycle, "MAX_CALL_SECONDS", 0)

    async with client() as http:
        session_id = (await start(http)).json()["session"]["sessionId"]
        for _ in range(10):
            await asyncio.sleep(0)
            if store.get_session(session_id).ended:
                break

    session = store.get_session(session_id)
    assert session.ended is True
    assert session.ended_reason == "max_duration"
