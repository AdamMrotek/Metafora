"""The capture pass: tool calls only, never spoken.

gpt-oss emits *either* speech or a tool call in a turn, never both — a property
of the harmony format, not of the prompt. Re-verified against the live API on
2026-08-23: given a conversational system prompt, a patient's answer and the
`update_intake` schema, it returned a tool call and an empty `content` in 5
trials out of 5.

So the work is split across two passes that run at the same time on the same
turn. The speech pass carries no tools, which means it always speaks and starts
streaming immediately; this pass writes the record silently while that audio is
still playing. The patient's clock only ever sees the speech pass.

Everything this pass writes as prose is discarded here, at the end of its
branch, so it can never reach the TTS.
"""

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class SilentBranch(FrameProcessor):
    """Swallows everything the capture pass says.

    Only text-bearing frames are dropped. System frames — start, end, cancel —
    still have to reach the sink or `ParallelPipeline` never completes, and the
    response-boundary frames are dropped too so the browser does not grow a
    second, silent speech bubble for a pass nobody hears.
    """

    _MUTED = (
        LLMTextFrame,
        TTSTextFrame,
        TTSSpeakFrame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
    )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, self._MUTED) and direction == FrameDirection.DOWNSTREAM:
            return
        await self.push_frame(frame, direction)
