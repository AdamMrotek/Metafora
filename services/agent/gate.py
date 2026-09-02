"""The safety gate as a pipeline stage.

`safety.py` holds the matcher and is deliberately free of any framework or
network import — a test asserts it. This module is the adapter that places it
in the media path.

Position is the whole design: between STT and the user context aggregator, so
the transcript passes through our code *before* it reaches the model. A
processor that returns without pushing its frame is a gate; one that pushes and
watches is an observer, and an observer cannot stop anything.
"""

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from services.agent.safety import scan
from services.agent.session_log import SafetyScanned, SessionWriter
from shared.contracts.models import ProtocolVersion


class SafetyGate(FrameProcessor):
    def __init__(
        self,
        protocol: ProtocolVersion,
        writer: SessionWriter,
        on_blocked=None,
        on_turn=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._protocol = protocol
        self._writer = writer
        self._on_blocked = on_blocked
        self._on_turn = on_turn
        self._closed = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Interim transcriptions are speculative and get revised; gating on them
        # would end calls on a half-heard word. The committed turn is the unit.
        if not isinstance(frame, TranscriptionFrame) or isinstance(
            frame, InterimTranscriptionFrame
        ):
            await self.push_frame(frame, direction)
            return

        result = scan(frame.text, self._protocol)
        self._writer.append(
            SafetyScanned(
                blocked=result.blocked,
                hits=[h.flag.id for h in result.hits],
                action=result.action,
            )
        )

        if not result.blocked:
            # The turn is real and it is about to become context for both
            # models, so this is where it is counted. A capture with no turn
            # behind it is a capture of nothing, and the machine refuses it
            # (`InterviewMachine.authorise`) — which is a rule that needs one
            # honest place to learn that the patient spoke. This is it, because
            # every transcript crosses it and a blocked one never gets past.
            if self._on_turn is not None:
                self._on_turn()
            await self.push_frame(frame, direction)
            return

        # Blocked. The frame is swallowed here and never becomes context, so
        # this turn reaches no model at all — that is the property the whole
        # in-the-media-path architecture exists to buy, and it is what the
        # session log proves after the fact.
        if self._closed:
            return
        self._closed = True

        # The reason crosses back to `services.core` on the writer — the bot has
        # no other handle on it, and that is deliberate (§8) — so the record
        # says "safety" and not "pipeline_finished".
        self._writer.note_end_reason("safety")

        # Speak first. A safety closure is the one thing a patient may not talk
        # over, and it is the last thing they hear, so it has to actually reach
        # them.
        if result.say:
            await self.push_frame(TTSSpeakFrame(result.say))

        # Then close, by draining rather than cancelling. Pushing an
        # EndWorkerFrame here tore the pipeline down before the TTS had
        # synthesised a word: the patient was left in a silent call that never
        # ended, with no way to tell the gate had fired at all.
        if self._on_blocked:
            await self._on_blocked(result)
