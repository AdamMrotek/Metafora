"""The gate must run before generation, and a blocked turn must reach no model.

`loop.test.ts` asserted this by counting model calls on a red-flag turn. The
pipeline equivalent is that the frame is never pushed downstream — downstream
being, in the real pipeline, the context aggregator and the LLM.
"""

import pytest
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from services.agent.config.protocol import WARMUP_V1
from services.agent.gate import SafetyGate
from services.agent.session_log import SafetyScanned


class RecordingWriter:
    def __init__(self):
        self.events = []
        self.ending_reason = None

    def append(self, event):
        self.events.append(event)

    def note_end_reason(self, reason):
        self.ending_reason = reason


@pytest.fixture
def gate_and_pushed():
    writer = RecordingWriter()
    closed: list = []

    async def on_blocked(result):
        closed.append(result)

    gate = SafetyGate(WARMUP_V1, writer, on_blocked=on_blocked)
    gate.closed = closed  # type: ignore[attr-defined]
    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    gate.push_frame = capture  # type: ignore[method-assign]
    return gate, pushed, writer


def transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="patient", timestamp="")


async def test_a_clean_turn_passes_through_to_the_model(gate_and_pushed):
    gate, pushed, writer = gate_and_pushed
    frame = transcription("my day is going well thanks")
    await gate.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert [f for f, _ in pushed] == [frame], "a clean turn must reach the model unchanged"
    assert [e for e in writer.events if isinstance(e, SafetyScanned)][0].blocked is False


async def test_a_blocked_turn_never_reaches_the_model(gate_and_pushed):
    """The whole point of being in the media path."""
    gate, pushed, _ = gate_and_pushed
    await gate.process_frame(
        transcription("sometimes I feel like I want to die"), FrameDirection.DOWNSTREAM
    )

    assert not any(isinstance(f, TranscriptionFrame) for f, _ in pushed), (
        "the transcript was forwarded downstream — the gate leaked"
    )


async def test_a_blocked_turn_speaks_the_protocol_sentence_and_ends_the_call(gate_and_pushed):
    gate, pushed, writer = gate_and_pushed
    await gate.process_frame(
        transcription("sometimes I feel like I want to die"), FrameDirection.DOWNSTREAM
    )

    spoken = [f for f, _ in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(spoken) == 1
    assert spoken[0].text == WARMUP_V1.red_flags[0].say, (
        "the sentence must come from the protocol, not from a model"
    )

    # The call ends by draining, not by cancelling. Pushing an end frame here
    # tore the pipeline down before the TTS had synthesised a word, which left
    # the patient in a silent call that never ended — the gate had fired and
    # they had no way to tell.
    assert gate.closed, "a blocked turn must ask for the call to be closed"
    assert gate.closed[0].say == WARMUP_V1.red_flags[0].say

    # The record says "safety", not "pipeline_finished": the reason crosses the
    # boundary on the writer, like every pipeline-initiated end.
    assert writer.ending_reason == "safety"


async def test_every_turn_is_logged_whether_or_not_it_trips(gate_and_pushed):
    gate, _, writer = gate_and_pushed
    for text in ["fine thanks", "a bit tired", "sometimes I feel like I want to die"]:
        await gate.process_frame(transcription(text), FrameDirection.DOWNSTREAM)

    scans = [e for e in writer.events if isinstance(e, SafetyScanned)]
    assert len(scans) == 3, "the audit trail records the scan, not just the hits"
    assert [s.blocked for s in scans] == [False, False, True]
    assert scans[-1].hits == ["rf_self_harm"]


async def test_the_call_ends_only_once(gate_and_pushed):
    """A second red flag after the call is closing must not re-end it."""
    gate, pushed, _ = gate_and_pushed
    for _ in range(2):
        await gate.process_frame(
            transcription("I want to die"), FrameDirection.DOWNSTREAM
        )
    assert len(gate.closed) == 1
    assert len([f for f, _ in pushed if isinstance(f, TTSSpeakFrame)]) == 1


async def test_interim_transcriptions_do_not_trip_the_gate(gate_and_pushed):
    """Interim results get revised; ending a call on a half-heard word is worse
    than waiting for the committed turn."""
    from pipecat.frames.frames import InterimTranscriptionFrame

    gate, pushed, writer = gate_and_pushed
    frame = InterimTranscriptionFrame(text="I want to die", user_id="patient", timestamp="")
    await gate.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert not gate.closed
    assert not [e for e in writer.events if isinstance(e, SafetyScanned)]
