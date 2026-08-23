"""The browser-facing messages.

`frontend/call` is unchanged by the migration, so these shapes are frozen by
`useCall.ts`: it keys bubbles by id and *replaces* rather than appends, drives
the call bar from server phase, and ignores the LiveKit topic entirely.
"""

import json

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from services.agent.wire import Wire, WireObserver


class FakeTransport:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, payload: str):
        self.sent.append(json.loads(payload))


def tts(text: str) -> TTSTextFrame:
    return TTSTextFrame(text=text, aggregated_by="sentence")


def pushed(frame) -> FramePushed:
    return FramePushed(
        source=None, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )


def make():
    transport = FakeTransport()
    return WireObserver(Wire(transport)), transport


async def test_a_frame_crossing_several_links_is_counted_once():
    """`on_push_frame` fires per processor hop. Counting a TTSTextFrame once
    per link repeated whole sentences into the patient's transcript."""
    observer, transport = make()
    frame = tts("I'm glad to hear that, Alice.")

    for _ in range(3):  # TTS -> transport -> aggregator
        await observer.on_push_frame(pushed(frame))

    utterances = [m for m in transport.sent if m["t"] == "utterance"]
    assert len(utterances) == 1
    assert utterances[0]["text"] == "I'm glad to hear that, Alice."


async def test_spoken_sentences_accumulate_into_one_bubble():
    observer, transport = make()
    await observer.on_push_frame(pushed(LLMFullResponseStartFrame()))
    for text in ["I'm glad to hear that, Alice.", "Take care."]:
        await observer.on_push_frame(pushed(tts(text)))

    utterances = [m for m in transport.sent if m["t"] == "utterance"]
    assert [u["text"] for u in utterances] == [
        "I'm glad to hear that, Alice.",
        "I'm glad to hear that, Alice. Take care.",
    ]
    # One id, so the browser replaces the bubble rather than appending a second.
    assert len({u["id"] for u in utterances}) == 1


async def test_a_new_response_starts_a_new_bubble_with_no_carryover():
    """The text of one reply must not leak into the next."""
    observer, transport = make()
    await observer.on_push_frame(pushed(LLMFullResponseStartFrame()))
    await observer.on_push_frame(pushed(tts("First reply.")))
    await observer.on_push_frame(pushed(LLMFullResponseEndFrame()))
    await observer.on_push_frame(pushed(tts("Second reply.")))

    utterances = [m for m in transport.sent if m["t"] == "utterance"]
    assert utterances[-1]["text"] == "Second reply."
    assert len({u["id"] for u in utterances}) == 2


async def test_two_identical_sentences_are_both_kept():
    """Dedupe is by frame identity, not by text — a patient may hear the same
    sentence twice and the transcript must show it twice."""
    observer, transport = make()
    await observer.on_push_frame(pushed(LLMFullResponseStartFrame()))
    await observer.on_push_frame(pushed(tts("Take care.")))
    await observer.on_push_frame(pushed(tts("Take care.")))

    assert [m for m in transport.sent if m["t"] == "utterance"][-1][
        "text"
    ] == "Take care. Take care."


async def test_phase_is_only_published_when_it_changes():
    """`loop.ts` sent phase from nine places and drifted. One setter, and it
    stays quiet when nothing changed."""
    observer, transport = make()
    for _ in range(3):
        await observer.on_push_frame(pushed(BotStartedSpeakingFrame()))

    assert [m for m in transport.sent if m["t"] == "phase"] == [
        {"t": "phase", "phase": "speaking"}
    ]


async def test_the_committed_transcript_becomes_a_patient_bubble():
    observer, transport = make()
    await observer.on_push_frame(
        pushed(TranscriptionFrame(text="pretty good thanks", user_id="p", timestamp=""))
    )
    utterance = next(m for m in transport.sent if m["t"] == "utterance")
    assert utterance["who"] == "patient"
    assert utterance["text"] == "pretty good thanks"
    # ...and the bar moves off "listening" once we have something to think about.
    assert {"t": "phase", "phase": "thinking"} in transport.sent


async def test_a_send_failure_never_takes_down_the_call():
    class Broken:
        async def send_message(self, payload):
            raise RuntimeError("data channel closed")

    wire = Wire(Broken())
    await wire.phase("speaking")  # must not raise


async def test_nothing_is_published_after_the_call_ends():
    """The pipeline is still draining when the call closes — the safety line is
    mid-sentence — and those frames were flipping the browser from "ended" back
    to "listening", which reads as a call that is somehow still going."""
    transport = FakeTransport()
    wire = Wire(transport)

    await wire.phase("speaking")
    await wire.ended("safety", "…please call 999.")
    await wire.phase("listening")
    await wire.notes([])
    await wire.ended("complete")

    kinds = [m["t"] for m in transport.sent]
    assert kinds == ["phase", "ended"], kinds
    assert transport.sent[-1]["reason"] == "safety"
