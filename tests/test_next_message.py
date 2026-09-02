"""The record and the reply are one decision.

The property that matters is an *ordering*, not a behaviour. The sentence the
patient hears is an argument of the call that writes the record, so it can only
be released after `tools.dispatch` has ruled on that call — which is what makes
the closing question survivable. Under the two-pass split this file replaced,
"is there anything else?" was answered by two models: one recorded
`anything_else`, completing the interview, while the other asked the patient
what they wanted to say. Here there is one answer, and if it stopped the call it
stops before its own sentence is spoken.
"""

import pytest
from pipecat.frames.frames import (
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from services.agent.config.protocol import PROTOCOLS, WARMUP_V1
from services.agent.machine import InterviewMachine
from services.agent.next_message import NextMessage, sentence, speakable


@pytest.fixture
def processor_and_pushed():
    processor = NextMessage()
    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    processor.push_frame = capture  # type: ignore[method-assign]
    return processor, pushed


def result_frame(
    *,
    message_next: str | None = "Glad to hear it. How have you been sleeping?",
    result=None,
    tool_call_id: str = "call_1",
) -> FunctionCallResultFrame:
    arguments: dict = {"field": "day_mood", "value": "pretty good"}
    if message_next is not None:
        arguments["message_next"] = message_next
    return FunctionCallResultFrame(
        function_name="update_intake",
        tool_call_id=tool_call_id,
        arguments=arguments,
        result=result if result is not None else {"ok": True, "recorded": "day_mood"},
        run_llm=False,
    )


def spoken(pushed) -> list[str]:
    return [f.text for f, _ in pushed if isinstance(f, LLMTextFrame)]


async def push(processor, frame, direction=FrameDirection.DOWNSTREAM):
    await processor.process_frame(frame, direction)


# ─── the sentence is released ────────────────────────────────────────────────


async def test_a_recorded_answer_speaks_the_sentence_it_arrived_with(processor_and_pushed):
    processor, pushed = processor_and_pushed
    await push(processor, result_frame())

    assert spoken(pushed) == ["Glad to hear it. How have you been sleeping?"]


async def test_the_sentence_is_framed_as_a_response(processor_and_pushed):
    """Bracketed by our own start/end, and deliberately not a `TTSSpeakFrame`.

    A `TTSSpeakFrame` is its own TTS turn context and the output transport
    raises a `BotStoppedSpeakingFrame` per context, so a two-sentence goodbye
    sent that way would let `EndOfInterview` hang up on the first sentence. As a
    response it chunks through `OrpheusAggregator` and stops once, at the end.

    The brackets are ours rather than the model's because a tool result arrives
    on the LLM service's own function-call task and may land after that turn's
    `LLMFullResponseEndFrame`. Text pushed loose after a response has closed
    sits unflushed in the TTS aggregator until the next one — spoken a turn
    late, to a patient who has moved on.
    """
    processor, pushed = processor_and_pushed
    await push(processor, result_frame())

    kinds = [
        type(f)
        for f, _ in pushed
        if isinstance(
            f, (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame)
        )
    ]
    assert kinds == [LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame]


async def test_the_result_still_reaches_the_context(processor_and_pushed):
    """The model is owed its result whatever we do with the sentence."""
    processor, pushed = processor_and_pushed
    frame = result_frame()
    await push(processor, frame)

    assert pushed[0][0] is frame, "the tool result must be forwarded, and first"


# ─── and withheld ────────────────────────────────────────────────────────────


async def test_a_refused_capture_withholds_the_sentence(processor_and_pushed):
    """The machine did not move, so a sentence about the next question is about
    a question the interview has not reached."""
    processor, pushed = processor_and_pushed
    await push(
        processor,
        result_frame(result={"ok": False, "error": "not allowed from warmup.q1"}),
    )

    assert spoken(pushed) == []


async def test_a_call_that_carries_no_usable_sentence_says_nothing(processor_and_pushed):
    """`sentence()`'s whole surface, through the processor that depends on it. A
    tool call with nothing sayable in it must leave the patient in silence
    rather than push an empty response through the TTS."""
    processor, pushed = processor_and_pushed
    for message_next in (None, "   ", ""):
        await push(processor, result_frame(message_next=message_next, tool_call_id="x"))
    assert spoken(pushed) == []
    assert sentence({"message_next": 7}) is None, "not a string is not a sentence"
    assert sentence('{"message_next": "hello"}') is None, "arguments must be a dict"
    assert sentence(None) is None


# ─── everything else passes through ──────────────────────────────────────────


async def test_a_plain_turn_is_untouched(processor_and_pushed):
    """A follow-up question records nothing and calls nothing; the model's text
    streams the way any reply does."""
    processor, pushed = processor_and_pushed
    text = LLMTextFrame("Of course — what's on your mind?")
    await push(processor, LLMFullResponseStartFrame())
    await push(processor, text)
    await push(processor, LLMFullResponseEndFrame())

    assert [f for f, _ in pushed] == [
        pushed[0][0],
        text,
        pushed[2][0],
    ], "a text turn must cross this processor unchanged"
    assert spoken(pushed) == ["Of course — what's on your mind?"]


async def test_an_unrelated_frame_is_forwarded(processor_and_pushed):
    processor, pushed = processor_and_pushed
    frame = TranscriptionFrame(text="pretty good", user_id="patient", timestamp="")
    await push(processor, frame)

    assert [f for f, _ in pushed] == [frame]


# ─── the predicate, on its own ───────────────────────────────────────────────


def test_speakable_is_permissive_about_a_result_it_does_not_recognise():
    """A handler that returned something else has not said the call is over."""
    assert speakable("recorded") is True
    assert speakable({"ok": True}) is True
    assert speakable({"ok": False}) is False
    assert speakable({"ok": True, "interview": "ending"}) is False


# ─── the closing question, end to end ────────────────────────────────────────
#
# Everything above is the processor on its own. These drive the real `dispatch`
# into the real `NextMessage`, because the property is the *order* of those two
# and neither half shows it alone.


class RecordingWriter:
    def __init__(self):
        self.events = []
        self.ending_reason = None

    def append(self, event):
        self.events.append(event)

    def note_end_reason(self, reason):
        self.ending_reason = reason


class RecordingWire:
    async def notes(self, fields):
        pass


async def a_turn(machine, writer, processor, pushed, *, tool_call_id, **arguments):
    """One patient turn: recorded by `dispatch`, then spoken by `NextMessage` —
    in that order, which is the whole design."""
    from services.agent.tools import dispatch

    machine.note_turn()
    result = await dispatch(
        machine=machine,
        writer=writer,
        wire=RecordingWire(),
        tool_name="update_intake",
        arguments=arguments,
    )
    await push(
        processor,
        FunctionCallResultFrame(
            function_name="update_intake",
            tool_call_id=tool_call_id,
            arguments=arguments,
            result=result,
            run_llm=False,
        ),
    )
    return result


async def test_the_closing_question_says_goodbye_and_completes_in_one_call(
    processor_and_pushed,
):
    """The failure the single pass exists to remove.

    Under the split: the patient says *yes* to "is there anything else?", the
    capture pass records `anything_else` — the last field, so the interview is
    complete and `EndOfInterview` hangs up on the next silence — and the speech
    pass, which cannot see that, asks them what it is. The line drops on the
    answer.

    Here the answer and the goodbye are the same emission. The record is
    written, the machine reaches `complete`, and the sentence released behind it
    was written by the model that had just recorded the last field.
    """
    processor, pushed = processor_and_pushed
    machine, writer = InterviewMachine(WARMUP_V1), RecordingWriter()

    await a_turn(
        machine, writer, processor, pushed,
        tool_call_id="call_1",
        field="day_mood",
        value="pretty good",
        message_next="Glad to hear it. Before we finish, is there anything else?",
    )
    assert spoken(pushed)[-1] == "Glad to hear it. Before we finish, is there anything else?"
    assert not machine.complete

    await a_turn(
        machine, writer, processor, pushed,
        tool_call_id="call_2",
        field="anything_else",
        value="there's some redness around the wound",
        message_next="Thank you for telling me — I'll pass that on. Take care, goodbye.",
    )

    assert machine.complete, "the last field is in, so the interview is over"
    assert spoken(pushed)[-1] == (
        "Thank you for telling me — I'll pass that on. Take care, goodbye."
    ), "the goodbye must be spoken, and it must be the goodbye for what they just said"


async def test_an_answer_that_stops_the_call_is_never_answered_by_the_model(
    processor_and_pushed,
):
    """`proto_preop_check_v2` authors an `end_call` on the very first question,
    which is the other half of the same problem: the patient says they cannot
    come, and under the split the speech pass asks them to confirm Thursday
    while the capture pass ends the call.

    The model still writes a sentence — it is not told a concern was raised, and
    must not be — but that sentence is released by the tool result, and this one
    retracts it. `EndOfInterview` says the protocol's closure instead.
    """
    processor, pushed = processor_and_pushed
    protocol = PROTOCOLS["proto_preop_check_v2"]
    machine, writer = InterviewMachine(protocol), RecordingWriter()

    result = await a_turn(
        machine, writer, processor, pushed,
        tool_call_id="call_1",
        field="attendance",
        value="no, I don't really feel like coming",
        answer="cannot_attend",
        flag="qf_attendance_cannot",
        message_next="I hear you. Have you got someone to take you home afterwards?",
    )

    assert result["interview"] == "ending"
    assert spoken(pushed) == [], (
        "the model's next question reached the TTS on a call that is ending"
    )
    assert not machine.complete, (
        "the interview stopped at this question; it did not finish"
    )
