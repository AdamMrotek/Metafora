"""Ending the interview — because the last answer is in, or because one of the
answers meant there was no point asking the rest.

The line closes on a sentence, not a submit. Nothing is submitted at the end,
because everything was saved as it was said — so this processor's whole job is
to let the assistant finish speaking and then hang up.

It sits between the LLM and the TTS so it can see a response end, but it waits
for `BotStoppedSpeakingFrame` before ending: cutting the audio at the moment the
model stops generating would clip the goodbye off the end of the call.

`stop()` is the second ending, and it is doing more work. A `QuestionFlag` at
`end_call` is decided by the capture pass, which runs *beside* the speech pass
rather than before it — so at the moment the concern is known, the speech pass
is already answering the same turn, and it is answering it as though the
interview were carrying on. The gate never has this problem: it runs before
generation, so on its path nothing is being said and it can push its sentence
straight out (`gate.py`).

Here something is being said, and it is the wrong thing. On the call this was
built for, the patient said *"I don't really feel like coming"* and heard:

    I hear you're not feeling up to it. Do you think you'll still be able to
    make it Thursday at seven?
    I'm sorry to hear that. I'll stop here rather than take you through the
    rest, and I'll make sure the unit contacts you to rebook.

The first sentence is not a bug in the speech pass — it is `system_prompt`'s
"if an answer is unclear, ask once more", working exactly as written, on a turn
the other pass had already read as a cancellation. Two models, one turn, and
only one of them can be right about what happens next.

So the speech pass loses. From `stop()` onward this processor:

  · **swallows** every word the speech pass has not already handed to the TTS —
    it sits between the models and the TTS, so that is simply not forwarding;
  · **interrupts**, which flushes whatever the TTS already holds and cancels the
    generation still in flight — the same mechanism barge-in uses, because this
    is the same event: something more important than the current utterance;
  · then speaks the authored sentence on its own, and hangs up behind it.

What the patient hears therefore depends on a race this cannot win outright, and
it is worth being plain about which way it goes. If the concern lands before the
first sentence reaches the TTS — the common case, because a tool call is short
and the speech pass is still streaming prose — they hear only the closure. If it
lands mid-utterance they hear a clipped fragment and then the closure. Neither
outcome leaves the assistant asking a question it is about to refuse to hear the
answer to, which is the one that cannot stand.

The authored sentence is never generated. It is the protocol's, spoken verbatim,
for the same reason the gate's is: what a patient hears when a call stops is not
something a model gets to phrase.
"""

import asyncio
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ControlFrame,
    EndWorkerFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from services.agent.config import tuning
from services.agent.machine import InterviewMachine
from services.agent.session_log import SessionWriter


@dataclass
class _ConcernStopped(ControlFrame):
    """Carries the decision from the capture branch onto this processor's task.

    `stop()` and `answered()` are awaited inside the tool handler, which runs on
    the capture branch. Interrupting from there would be this pipeline
    cancelling the task it is currently executing on. Queueing instead means
    every frame decision is made in `process_frame` like all the others, and the
    tool handler returns normally to a model that is owed its result.
    """


@dataclass
class _HoldOver(ControlFrame):
    """Release the held reply: the capture pass has ruled, or its time is up."""


class EndOfInterview(FrameProcessor):
    def __init__(
        self,
        machine: InterviewMachine,
        writer: SessionWriter,
        wire=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._machine = machine
        self._writer = writer
        self._wire = wire
        self._closed = False
        #: The authored sentence a blocking concern is waiting to say. Set from
        #: the tool handler, off this task — which is why the interruption it
        #: triggers is queued rather than pushed.
        self._say: str | None = None
        #: Spoken. From here the speech pass is silenced, so nothing else can
        #: reach the patient between this sentence and the end of the call.
        self._said = False
        #: The closure is audibly under way. Needed because the interruption
        #: that clears the way for it produces a `BotStoppedSpeakingFrame` of
        #: its own, and ending on that one would cut off the sentence it was
        #: raised to make room for.
        self._saying = False
        #: The reply, held back while the capture pass rules on a question that
        #: can stop the call. `None` means nothing is being held — which is
        #: every turn of every other question. See `tuning.CONCERN_HOLD_MS`.
        self._held: list[tuple[Frame, FrameDirection]] | None = None
        self._deadline: asyncio.Task | None = None

    #: What the speech pass says reaches the TTS through here. While a concern
    #: is stopping the call, none of it does; while one is being decided, it
    #: waits. `LLMFullResponseEndFrame` is in the list because releasing the end
    #: of a response before its text would close it around nothing.
    _SPEECH = (LLMTextFrame, TTSTextFrame, TTSSpeakFrame, LLMFullResponseEndFrame)

    def _can_stop_the_call(self) -> bool:
        """Whether the question being answered right now is one whose flags
        include an `end_call`.

        The hold costs a patient time, so it is spent only where the answer
        could make the reply wrong. On every other question the two passes may
        finish in either order and it changes nothing.
        """
        state = self._machine.current
        return state is not None and any(f.action == "end_call" for f in state.question.flags)

    async def answered(self, result) -> None:
        """One authorised capture, whatever it raised. Called from `dispatch`.

        This is the signal the hold is waiting for, and it has to arrive on
        every capture rather than only the blocking ones — a reply held for a
        concern that did not come is a reply nobody ever hears.
        """
        if result.blocked:
            await self.stop(result)
            return
        if self._held is not None:
            await self.queue_frame(_HoldOver())

    async def stop(self, result) -> None:
        """A question flag stopped the interview. Called from `tools.dispatch`.

        Records the reason immediately, because that has to survive however the
        call actually unwinds, and hands the rest to this processor's own task.
        The first `stop` wins: a second concern on the same answer does not get
        a second goodbye.
        """
        if self._say is not None or self._closed:
            return
        self._say = result.say
        # Same vocabulary the gate files, and deliberately so: from the record's
        # side this is a call the system stopped, and the dashboard should not
        # need a second word for that. Which flag stopped it is on
        # `concern.raised`, which is where the difference belongs.
        self._writer.note_end_reason("safety")
        logger.info("[end] concern stops the interview")
        # From this line the speech pass is already being swallowed, whichever
        # task gets here first.
        await self.queue_frame(_ConcernStopped())

    async def _open_hold(self) -> None:
        self._held = []
        self._deadline = asyncio.create_task(self._expire())

    async def _expire(self) -> None:
        """The bound on the wait. Past it the reply is released and the call
        behaves as it did before the hold existed."""
        try:
            await asyncio.sleep(tuning.CONCERN_HOLD_MS / 1000)
        except asyncio.CancelledError:
            return
        logger.info("[end] capture pass did not rule in time — releasing the reply")
        await self.queue_frame(_HoldOver())

    async def _release(self) -> None:
        """Everything held, in the order it arrived, or nothing if it is moot."""
        held, self._held = self._held or [], None
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None
        if self._say is not None:
            return  # a concern is stopping the call; none of this is said
        for frame, direction in held:
            await self.push_frame(frame, direction)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # A new reply on a question that can stop the call. Hold it until the
        # other pass has had its say — normally it already has, and the hold
        # closes in the same breath it opened.
        if (
            isinstance(frame, LLMFullResponseStartFrame)
            and self._held is None
            and self._say is None
            and not self._closed
            and self._can_stop_the_call()
        ):
            await self._open_hold()

        if isinstance(frame, _HoldOver):
            await self._release()
            return

        # Anything the speech pass is still producing is an answer to a question
        # the interview is no longer asking. Dropped before it is forwarded,
        # which is the whole reason this processor sits above the TTS.
        if self._say is not None and not self._said and isinstance(frame, self._SPEECH):
            return

        if self._held is not None and isinstance(frame, self._SPEECH):
            self._held.append((frame, direction))
            return

        if isinstance(frame, _ConcernStopped):
            # Not forwarded: it is a message to this processor, not a frame the
            # pipeline has any use for.
            if self._closed or self._said:
                return
            self._said = True
            await self._release()  # discards the held reply; `_say` is set
            # Flush what the TTS already holds and cancel what is still being
            # generated — barge-in's mechanism, because this is barge-in's
            # situation with the roles reversed. Only reaches anything when the
            # hold lost its race; when it won, there is nothing out there to cut.
            await self.broadcast_interruption()
            await self.push_frame(TTSSpeakFrame(self._say))
            return

        await self.push_frame(frame, direction)

        if self._closed:
            return

        if self._said:
            # The interruption stops the bot speaking too, so the first
            # `BotStoppedSpeakingFrame` after it is the *old* sentence ending,
            # not the closure. Wait to hear the closure start.
            if isinstance(frame, BotStartedSpeakingFrame):
                self._saying = True
                return
            if not (self._saying and isinstance(frame, BotStoppedSpeakingFrame)):
                return
            self._closed = True
            if self._wire is not None:
                await self._wire.notes(self._machine.fields())
                await self._wire.phase("ended")
                await self._wire.ended("safety", self._say)
            await self.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
            return

        if not isinstance(frame, BotStoppedSpeakingFrame):
            return
        if not self._machine.complete:
            return

        # Every question has an answer and the assistant has finished saying
        # goodbye. There is nothing left for the patient to do.
        self._closed = True
        logger.info("[end] interview complete")
        if self._wire is not None:
            await self._wire.notes(self._machine.fields())
            await self._wire.phase("ended")
            await self._wire.ended("complete")
        await self.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
