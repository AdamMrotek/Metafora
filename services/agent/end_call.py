"""Ending the interview once the last answer is in.

The line closes on a sentence, not a submit. Nothing is submitted at the end,
because everything was saved as it was said — so this processor's whole job is
to let the assistant finish speaking and then hang up.

It sits between the LLM and the TTS so it can see a response end, but it waits
for `BotStoppedSpeakingFrame` before ending: cutting the audio at the moment the
model stops generating would clip the goodbye off the end of the call.
"""

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    Frame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from services.agent.machine import InterviewMachine
from services.agent.session_log import SessionWriter


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

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if self._closed or not isinstance(frame, BotStoppedSpeakingFrame):
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
