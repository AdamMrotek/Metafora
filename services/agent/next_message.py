"""The turn, released: one model, one emission, the record and the reply.

gpt-oss emits speech *or* a tool call and never both. That used to be answered
by splitting the turn across two passes running side by side — one that spoke
and held no tools, one that held the tools and was never heard. It bought an
immediate first sentence, and it cost the one thing a clinical interview cannot
afford: the two passes could not see each other. They answered the same turn
from two contexts and nothing reconciled the two answers.

The closing question is where that bill arrived. "Is there anything else you
want to talk about?" — the patient says *yes*; the capture pass records
`anything_else`, which is the last field, so the interview is complete and the
call ends; the speech pass, which never saw that, asks *"what would you like to
tell me?"* and the line drops on the answer.

This module is the other design, and now the only one. One pass, holding the
tools, told to answer *through* them: the arguments carry the field being
recorded **and** `message_next`, the sentence to say once it is recorded. So:

    patient: "pretty good"
      → update_intake(day_mood, "pretty good", message_next="Glad to hear it.
        Is there anything else you'd like to talk about?")            ← spoken
    patient: "yes"
      → plain text: "Of course — what's on your mind?"                ← spoken
    patient: "there's redness around the wound"
      → update_intake(anything_else, "redness around the wound",
        flag=..., message_next="Thank you for telling me. Take care,
        goodbye.")                                                     ← spoken,
        and the interview is complete behind it

The ordering is what makes this safe. The record is written first, in
`tools.dispatch`, and only then is the sentence released — so a capture that
stops the call (a `QuestionFlag` at `end_call`) suppresses the model's sentence
before it is ever synthesised, and the protocol's authored closure is spoken in
its place. There is no race to lose: the answer and the reply were one
decision, and this processor is downstream of it.

What it costs is the first token. A tool-less speech pass starts streaming prose
immediately; here nothing is spoken until the whole tool call has been
generated, because `message_next` is its last argument. That is roughly one JSON
object of latency, paid once per recorded turn, against a class of failure that
cannot otherwise be removed.

Turns that record nothing are untouched. A follow-up question, a clarification,
a patient who asked something back — the model answers in plain text and it
streams the way any reply does.
"""

from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: The argument carrying what to say. Named once, here, because the schema in
#: `machine.tool_definitions` and the instruction in `prompts.system_prompt`
#: have to agree with this file or the sentence is written and never spoken.
MESSAGE_NEXT = "message_next"


def sentence(arguments: Any) -> str | None:
    """The sentence a tool call is carrying, if it is carrying one."""
    if not isinstance(arguments, dict):
        return None
    text = arguments.get(MESSAGE_NEXT)
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def speakable(result: Any) -> bool:
    """Whether the model's sentence still stands, given what its call did.

    Two results retract it, and both are the point of doing it in this order:

    · a **refused** capture — the permission matrix said no, so the machine did
      not move and a sentence written for the question after this one would be
      asking about a field that was never recorded;
    · an **ending** one — a concern stopped the interview, and what a patient
      hears when a call stops is the protocol's sentence, never a model's.
      `EndOfInterview` speaks it.
    """
    if not isinstance(result, dict):
        return True
    if result.get("ok") is False:
        return False
    return result.get("interview") != "ending"


class NextMessage(FrameProcessor):
    """Releases `message_next` into the speech path as an ordinary reply.

    Deliberately *not* a `TTSSpeakFrame`. That frame is its own TTS turn
    context, and the output transport raises `BotStoppedSpeakingFrame` per turn
    context — so a two-sentence goodbye sent as two `TTSSpeakFrame`s would let
    `EndOfInterview` hang up on the first one. Framed as a response instead,
    the sentence goes through `OrpheusAggregator` like every other reply: same
    chunking, same 200-character cap, same single stop at the end of it. Nothing
    downstream has to know where the words came from.

    The response boundaries are ours rather than the model's because the tool
    result arrives on the LLM service's function-call task, which may land
    after that turn's own `LLMFullResponseEndFrame`. Text pushed loose after a
    response has closed sits in the TTS aggregator unflushed until the next
    one — that is, it is spoken a turn late, to a patient who has moved on.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if not isinstance(frame, FunctionCallResultFrame):
            return
        # A result is broadcast both ways. Only the downstream copy travels
        # toward the TTS; the upstream one goes back to the aggregator, and
        # speaking off it would say the sentence twice.
        if direction != FrameDirection.DOWNSTREAM:
            return

        text = sentence(frame.arguments)
        if text is None:
            # A model that recorded a field and said nothing has left the
            # patient in silence. Worth a line in the log: it is the one
            # failure mode this design has that a plain speech pass does not.
            logger.warning(f"[next] {frame.function_name} carried no {MESSAGE_NEXT}")
            return

        if not speakable(frame.result):
            logger.info(f"[next] sentence withheld: {frame.result}")
            return

        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMTextFrame(text))
        await self.push_frame(LLMFullResponseEndFrame())
