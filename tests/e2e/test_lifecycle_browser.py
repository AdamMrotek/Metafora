"""What happens to a call when the patient's browser goes away.

Every test here is the same shape, because the property is the same one: the
box holds a small number of concurrent calls by construction
(`MAX_CONCURRENT_SESSIONS`, three), each one a WebRTC connection, a pipeline
task and a paid model on the other end — so a patient who has gone must stop
costing a slot, promptly, by every route out of the page.

    start a real call → assert the slot is held → leave → assert it is freed

The middle assertion is not a formality. Without it a broken hangup and a
session that died on its own are indistinguishable, and both read as green.

Why a browser rather than `tests/test_lifecycle.py`: the routes out of a page
are `pagehide`, an unload that kills in-flight requests, and a React cleanup
that runs on neither. None of those exist in an HTTP client.

Read `test_a_call_whose_media_never_connects_frees_the_slot` before adding to
this file. Measured against a real SFU on localhost, the three page-teardown
tests below pass with `hangUp`'s `POST /end` removed — `livekit-client` leaves
the room on `pagehide` by itself and the SFU reports it in about ten
milliseconds. They are worth keeping as statements of the property, but they
are not what holds the fix up, and a test that cannot fail is not evidence.

Run with `make test-e2e`. Deselected from `make test`, which stays keyless.
"""

import asyncio

import httpx
import pytest
from playwright.async_api import Page, expect

from tests.e2e.conftest import API

pytestmark = pytest.mark.browser

#: How long a hangup may take to reach the backend. Generous next to the
#: sub-second a `keepalive` POST needs, and still far under the SFU's own ICE
#: timeout — which is the thing this whole file exists to beat.
FREED_TIMEOUT_S = 10.0

#: The mic, `POST /session`, the assistant's own join and the browser's WebRTC
#: handshake. Nothing here waits on speech.
CONNECT_TIMEOUT_MS = 30_000

#: A data-channel message is published before anything else teardown does, so
#: this is the network and a React render, not a pipeline.
WIRE_TIMEOUT_MS = 10_000

#: The browser letting go of the room. Deliberately loose: the assistant leaves
#: only once it has stopped speaking, and it may be mid-greeting when the call
#: is ended — `GOODBYE_TIMEOUT_S` bounds that at ten seconds on the other side.
HANGUP_TIMEOUT_S = 20.0

#: A pipeline that has to finish speaking before it can stop. The opening
#: greeting and then whatever the ending queued behind it, both at real time.
DRAINED_TIMEOUT_S = 90.0


async def _start_call(page: Page, portal: str) -> None:
    """Tap the one button in the whole interview, and wait to be in the call.

    `App.tsx` swaps the opening screen for `.pp__body` when `call.session` is
    set, which happens only after `room.connect()` and the mic publish have
    both returned — so reaching it is the browser genuinely in the room, not a
    request that merely got a token back.
    """
    await page.goto(portal, wait_until="domcontentloaded")
    await page.get_by_role("button", name="Start the conversation").click()
    await expect(page.locator(".pp__body")).to_be_visible(timeout=CONNECT_TIMEOUT_MS)


async def test_a_reload_frees_the_slot(page: Page, portal: str, live, until):
    """A reload runs no React cleanup, and `room.disconnect()` is async.

    Two things could free the slot here — `livekit-client`'s own `pagehide`
    handler, or ours — and on localhost the first wins by a distance. So this
    holds the *property* rather than either mechanism: a reloaded call is over,
    however the backend came to hear about it.
    """
    await _start_call(page, portal)
    assert await live() == 1, "the call never started, so leaving it proves nothing"

    await page.reload(wait_until="domcontentloaded")

    took = await until(
        lambda: _is_free(live), FREED_TIMEOUT_S, "the reloaded call still holds a slot"
    )
    print(f"\n[e2e] reload freed the slot in {took:.2f}s")


async def test_closing_the_tab_frees_the_slot(page: Page, portal: str, live, until):
    """The harsher half of the same path: no reload, no next page, just gone.

    Playwright closes a tab politely, so this is the *graceful* version of a
    discarded tab — the page still gets its `pagehide`. A renderer killed
    outright runs no JavaScript at all, which nothing in this file can stage
    and only `PATIENT_TIMEOUT_S` catches.
    """
    await _start_call(page, portal)
    assert await live() == 1, "the call never started, so leaving it proves nothing"

    await page.close()

    await until(lambda: _is_free(live), FREED_TIMEOUT_S, "the closed tab still holds a slot")


async def test_navigating_away_frees_the_slot(page: Page, portal: str, live, until):
    """Following a link out of the portal is leaving, the same as closing it."""
    await _start_call(page, portal)
    assert await live() == 1, "the call never started, so leaving it proves nothing"

    await page.goto("about:blank", wait_until="domcontentloaded")

    await until(lambda: _is_free(live), FREED_TIMEOUT_S, "the abandoned call still holds a slot")


async def test_a_call_whose_media_never_connects_frees_the_slot(
    page: Page, portal: str, live, until
):
    """The gap the other three cannot reach, and the one the fix is for.

    Every route out of a *connected* page is already covered twice over:
    `livekit-client` hangs its own `disconnectOnPageLeave` off `pagehide`, the
    SFU reports the leave, and `on_participant_disconnected` tears the call
    down — measurably within a hundredth of a second on localhost. Which means
    those tests would pass with `hangUp`'s `POST /end` deleted.

    This one would not. `POST /session` has returned, so the bot is in the room
    and the slot is spent; the browser then fails to join it. There is no
    participant to disconnect, so the SFU has nothing to report and the backend
    has nothing to notice — the browser saying so is the only signal that
    exists, and before the fix there was none until `PATIENT_TIMEOUT_S`.

    Killing the signalling socket is how a call fails for a patient behind a
    firewall that permits HTTPS and nothing else, which is not a rare patient.
    """
    await page.route_web_socket("**/rtc**", lambda ws: ws.close())

    await page.goto(portal, wait_until="domcontentloaded")
    await page.get_by_role("button", name="Start the conversation").click()

    # The room is the bot's the moment `POST /session` returns, whatever the
    # browser then manages to do with the token.
    await until(lambda: _is_held(live), CONNECT_TIMEOUT_MS / 1000, "no call was ever started")

    # `start()`'s own catch is the hangup here: no reload, no navigation, just
    # a connection that refused. The patient is still sitting on the page.
    took = await until(
        lambda: _is_free(live), FREED_TIMEOUT_S, "a call that never connected still holds a slot"
    )
    print(f"\n[e2e] a failed connection freed the slot in {took:.2f}s")

    # And says so, rather than leaving them on a spinner.
    await expect(page.locator(".pp__error")).to_be_visible()


async def test_the_backend_tells_the_browser_the_call_is_over(page: Page, portal: str, live, until):
    """The other direction: not the browser hanging up, but being hung up on.

    Two things have to happen, and the earlier regression broke the first while
    leaving the second looking fine. `teardown` publishes `ended` over the
    LiveKit data channel *before* it stops the pipeline; the assistant then
    finishes its sentence and leaves the room, and the browser — seeing the one
    participant it had go — drains its jitter buffer and lets go.

    So this asserts the message arrived (it is rendered, so it crossed the real
    channel and was really decoded) and then that the signalling socket closed.
    A browser still holding that socket is a patient still sitting in a call
    that ended, which is what was reported.

    `POST /end` is the trigger because it is deterministic. A call that ends by
    completing an interview is the same code past a conversation.
    """
    signalling_closed = asyncio.Event()

    def _watch(ws) -> None:
        # `…:7880/rtc?…` is LiveKit's; the other socket on this page is Vite's.
        if "/rtc" in ws.url:
            ws.on("close", lambda _: signalling_closed.set())

    page.on("websocket", _watch)

    await page.goto(portal, wait_until="domcontentloaded")
    async with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/api/session")
    ) as started:
        await page.get_by_role("button", name="Start the conversation").click()
    session_id = (await (await started.value).json())["session"]["sessionId"]

    await expect(page.locator(".pp__body")).to_be_visible(timeout=CONNECT_TIMEOUT_MS)
    assert await live() == 1, "the call never started, so ending it proves nothing"

    async with httpx.AsyncClient(timeout=10.0) as http:
        ended = await http.post(f"{API}/session/{session_id}/end")
    assert ended.status_code == 200

    # ① told. `CallBar` renders this for phase `ended` and nothing else does.
    await expect(page.get_by_text("The call has ended")).to_be_visible(timeout=WIRE_TIMEOUT_MS)

    # ② and let go, rather than left holding a room with nobody in it.
    await asyncio.wait_for(signalling_closed.wait(), timeout=HANGUP_TIMEOUT_S)
    assert await live() == 0


async def test_a_call_the_pipeline_ends_itself_is_filed_under_why_it_ended(
    page: Page, portal: str, live, until, record
):
    """The ending that does not go through `teardown` — and the slowest test here.

    Every other ending in this file is something asking the backend to stop, so
    `end_session` runs first and settles the reason before anything else can.
    The safety gate is the other shape: it stops the call from *inside* the
    pipeline (`lifecycle._on_blocked` sets a reason and drains), and the reason
    is not settled until `_run`'s `finally`. Anything that ends the session in
    the gap files the call under its own name instead.

    Which is invisible from the page — the patient is told, hears the sentence
    and the line closes, exactly as it should. Only the record is wrong, and a
    safety closure recorded as a transport fault is the one this system can
    least afford to lose. So this asserts on the record, not the screen.

    The red flag is typed rather than spoken: `POST /session/{id}/typed` enters
    as a `TranscriptionFrame` and is gated identically, and `proving_utterance`
    makes the trigger a fixed string rather than a bet on a speech model.

    Slow (~40s) because `stop_when_done` drains what is queued before it ends —
    the opening greeting, then the sentence the gate queued.
    """
    from services.agent.config.protocol import SELF_HARM

    signalling_closed = asyncio.Event()

    def _watch(ws) -> None:
        if "/rtc" in ws.url:
            ws.on("close", lambda _: signalling_closed.set())

    page.on("websocket", _watch)

    await page.goto(portal, wait_until="domcontentloaded")
    async with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/api/session")
    ) as started:
        await page.get_by_role("button", name="Start the conversation").click()
    session_id = (await (await started.value).json())["session"]["sessionId"]
    await expect(page.locator(".pp__body")).to_be_visible(timeout=CONNECT_TIMEOUT_MS)

    # Through the page's own typed fallback, so the gate is reached the way a
    # patient reaches it.
    await page.get_by_role("button", name="type instead").click()
    await page.get_by_label("Type your answer").fill(SELF_HARM.proving_utterance)
    await page.get_by_role("button", name="Send").click()

    # ① the patient is told, and told promptly — the gate runs before any model.
    await expect(page.get_by_text("The call has ended")).to_be_visible(timeout=WIRE_TIMEOUT_MS)

    # ② the line closes once the sentence the gate queued has been spoken.
    await asyncio.wait_for(signalling_closed.wait(), timeout=DRAINED_TIMEOUT_S)
    await until(lambda: _is_free(live), FREED_TIMEOUT_S, "the closed call still holds a slot")

    # ③ and it is filed as a safety closure. This is the assertion: ① and ② hold
    # whether or not the reason survived.
    async def _filed() -> bool:
        return any(e.get("type") == "session.ended" for e in record(session_id))

    await until(_filed, DRAINED_TIMEOUT_S, "nothing was ever written down")
    ended = [e for e in record(session_id) if e.get("type") == "session.ended"]
    assert [e["reason"] for e in ended] == ["safety"]


async def test_abandoned_calls_do_not_fill_the_box(page: Page, portal: str, live, until):
    """The property the count is only a proxy for.

    Three abandoned calls used to mean the fourth patient was told every line
    was busy. Reloading repeatedly is the most ordinary thing a confused person
    does, so this walks past the capacity ceiling one reload at a time and
    asserts a call still connects on the far side of it.
    """
    from tests.e2e.conftest import capacity

    for _ in range(await capacity() + 1):
        await _start_call(page, portal)
        assert await live() == 1
        await page.reload(wait_until="domcontentloaded")
        await until(lambda: _is_free(live), FREED_TIMEOUT_S, "an abandoned call still holds a slot")

    # Nothing accumulated, so the box answers as readily as it did at the start.
    await _start_call(page, portal)
    assert await live() == 1


async def _is_free(live) -> bool:
    return await live() == 0


async def _is_held(live) -> bool:
    return await live() == 1
