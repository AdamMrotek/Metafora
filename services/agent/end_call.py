"""Ending the interview — because the last answer is in, or because one of the
answers meant there was no point asking the rest.

The line closes on a sentence, not a submit. Nothing is submitted at the end,
because everything was saved as it was said — so this processor's whole job is
to let the assistant finish speaking and then hang up.

It sits between the LLM and the TTS so it can see a response end, but it waits
for `BotStoppedSpeakingFrame` before ending: cutting the audio at the moment the
model stops generating would clip the goodbye off the end of the call.

`stop()` is the second ending, and it is doing more work. A `QuestionFlag` at
`end_call` is decided inside the tool handler, on the LLM service's own task,
while the pipeline is already carrying that turn's reply toward the TTS. The
gate never has this problem: it runs before generation, so on its path nothing
is being said and it can push its sentence straight out (`gate.py`).

Here something may already be being said, and it is the wrong thing. On the call
this was built for, the patient said *"I don't really feel like coming"* and
heard:

    I hear you're not feeling up to it. Do you think you'll still be able to
    make it Thursday at seven?
    I'm sorry to hear that. I'll stop here rather than take you through the
    rest, and I'll make sure the unit contacts you to rebook.

The first sentence was the pipeline's other half, working exactly as written, on
a turn this one had already read as a cancellation. That half is gone — the
sentence the patient hears is now an argument of the tool call itself, and
`next_message.py` withholds it when the result says the interview is ending, so
in the ordinary case nothing wrong is ever synthesised. What remains here is the
backstop for everything that was already in flight. From `stop()` onward this
processor:

  · **swallows** every word that has not already reached the TTS — it sits
    between the model and the TTS, so that is simply not forwarding;
  · **interrupts**, which flushes whatever the TTS already holds and cancels the
    generation still in flight — the same mechanism barge-in uses, because this
    is the same event: something more important than the current utterance;
  · then speaks the authored sentence on its own, and hangs up behind it.

The authored sentence is never generated. It is the protocol's, spoken verbatim,
for the same reason the gate's is: what a patient hears when a call stops is not
something a model gets to phrase.

One sentence can also follow the *ordinary* ending. A call on which an urgent
flag fired owes the patient a promise of contact, and it is said here, after the
goodbye, rather than when the flag fired: at that moment the model is mid-reply
and the two would race into the same TTS, whereas once the goodbye has finished
nothing else is generating. It is one bit on the machine rather than a `say` on
the flag, so a call carrying two urgent flags still ends on one sentence — and a
call this processor *stopped* never reaches it, because that call has its own
authored closure and must not also promise a call back it has just made.
"""

from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ControlFrame,
    EndWorkerFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from services.agent.machine import InterviewMachine
from services.agent.session_log import ClosureSpoken, SessionWriter


@dataclass
class _ConcernStopped(ControlFrame):
    """Carries the decision from the tool handler onto this processor's task.

    `stop()` and `answered()` are awaited inside the tool handler, which runs on
    the LLM service's function-call task. Interrupting from there would be this
    pipeline cancelling the task it is currently executing on. Queueing instead
    means every frame decision is made in `process_frame` like all the others,
    and the tool handler returns normally to a model that is owed its result.
    """


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
        #: Spoken. From here nothing else can reach the patient between this
        #: sentence and the end of the call.
        self._said = False
        #: The closure is audibly under way. Needed because the interruption
        #: that clears the way for it produces a `BotStoppedSpeakingFrame` of
        #: its own, and ending on that one would cut off the sentence it was
        #: raised to make room for.
        self._saying = False
        #: The same pair for the *other* authored sentence — the one an urgent
        #: flag owes a patient on a call that ran to the end. It is a separate
        #: pair rather than the same one because the two never happen together
        #: and mean different things: `_said` is a call this processor stopped,
        #: `_told` is a call that finished and had something to add.
        self._told = False
        self._telling = False

    #: What the model says reaches the TTS through here, and while a concern is
    #: stopping the call, none of it does. `LLMFullResponseEndFrame` is in the
    #: list because releasing the end of a response before its text would close
    #: it around nothing.
    _SPEECH = (LLMTextFrame, TTSTextFrame, TTSSpeakFrame, LLMFullResponseEndFrame)

    async def answered(self, result) -> None:
        """One authorised capture, whatever it raised. Called from `dispatch`,
        which cannot push a frame of its own."""
        if result.blocked:
            await self.stop(result)

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
        # From this line anything still in flight is already being swallowed,
        # whichever task gets here first.
        await self.queue_frame(_ConcernStopped())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Anything still in flight is an answer to a question the interview is
        # no longer asking. Dropped before it is forwarded, which is the whole
        # reason this processor sits above the TTS.
        if self._say is not None and not self._said and isinstance(frame, self._SPEECH):
            return

        if isinstance(frame, _ConcernStopped):
            # Not forwarded: it is a message to this processor, not a frame the
            # pipeline has any use for.
            if self._closed or self._said:
                return
            self._said = True
            # Flush what the TTS already holds and cancel what is still being
            # generated — barge-in's mechanism, because this is barge-in's
            # situation with the roles reversed. Usually there is nothing out
            # there to cut, because `next_message.py` withheld the sentence
            # before it was ever synthesised.
            await self.broadcast_interruption()
            await self.push_frame(TTSSpeakFrame(self._say))
            # Filed here rather than in `stop()`, which only decides: this is
            # the line that actually says it, and a sentence the record holds
            # but the patient never heard would be the worse of the two lies.
            # It reaches no `llm.completed` — a `TTSSpeakFrame` raises no text
            # frame — so without this the transcript has no copy of it.
            self._writer.append(ClosureSpoken(text=self._say))
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

        if not self._machine.complete:
            return

        # Every question has an answer. What is left is the goodbye — and then,
        # only on a call where an urgent flag fired, one more authored sentence.
        owed = self._machine.urgent_closing
        if owed is not None and not self._told:
            if not isinstance(frame, BotStoppedSpeakingFrame):
                return
            # Here and nowhere earlier. Nothing is generating at this point, so
            # this sentence cannot race the model's reply into the same TTS —
            # which is the whole reason it waits rather than speaking when the
            # flag fired.
            self._told = True
            logger.info("[end] urgent: the patient is told the unit will call")
            await self.push_frame(TTSSpeakFrame(owed))
            self._writer.append(ClosureSpoken(text=owed))
            return

        if self._told:
            # Its own turn context, so it raises its own speaking frames. Same
            # wait as the concern path above, for the same reason.
            if isinstance(frame, BotStartedSpeakingFrame):
                self._telling = True
                return
            if not (self._telling and isinstance(frame, BotStoppedSpeakingFrame)):
                return
        elif not isinstance(frame, BotStoppedSpeakingFrame):
            return

        # There is nothing left for the patient to do.
        self._closed = True
        logger.info("[end] interview complete")
        if self._wire is not None:
            await self._wire.notes(self._machine.fields())
            await self._wire.phase("ended")
            await self._wire.ended("complete")
        await self.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
