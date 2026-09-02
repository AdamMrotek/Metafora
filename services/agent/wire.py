"""Telling the browser what to draw.

`frontend/call` is not being changed by the migration, so this module's whole
job is to emit the four `ServerMessage` shapes it already speaks. It is an
Observer rather than a processor because none of it affects the conversation —
it watches frames go past and narrates them.

The call bar reflects server phase rather than local inference, because the
server is the only party that knows whether it is still generating. That is why
`phase` is pushed rather than guessed at in the browser.
"""

import uuid
from collections import OrderedDict

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from shared.contracts.models import CallPhase, FieldState
from shared.contracts.wire import (
    EndedMessage,
    NotesMessage,
    PhaseMessage,
    ServerMessage,
    UtteranceMessage,
    encode_message,
)


class Wire:
    """Publishes `ServerMessage`s on the LiveKit data channel.

    `LiveKitTransport.send_message` takes a string and publishes it reliably.
    The client ignores the LiveKit topic and simply JSON-parses the payload, so
    the discriminant `t` is the entire protocol.
    """

    def __init__(self, transport) -> None:
        self._transport = transport
        self._phase: CallPhase | None = None
        self._closed = False

    async def send(self, message: ServerMessage) -> None:
        # Nothing follows the end. The pipeline is still draining when the call
        # closes — the safety line is mid-sentence — and those frames were
        # repainting a browser that had already been told the call was over.
        if self._closed:
            return
        try:
            await self._transport.send_message(encode_message(message))
        except Exception as exc:  # a UI update must never take down a live call
            logger.warning(f"[wire] send failed: {exc}")

    async def phase(self, phase: CallPhase) -> None:
        # One setter that publishes, so internal state and what the browser
        # draws cannot drift apart. `loop.ts` sent this from nine places.
        #
        if phase == self._phase:
            return
        self._phase = phase
        await self.send(PhaseMessage(phase=phase))

    async def notes(self, fields: list[FieldState]) -> None:
        await self.send(NotesMessage(fields=fields))

    async def utterance(self, who: str, text: str, id: str) -> None:
        await self.send(UtteranceMessage(who=who, text=text, id=id))

    async def ended(self, reason: str, say: str | None = None) -> None:
        if self._closed:
            return
        await self.send(EndedMessage(reason=reason, say=say))
        # The line closes on a sentence, not a submit; after it, the browser is
        # told nothing more.
        self._closed = True


class WireObserver(BaseObserver):
    """Derives the four browser messages from frames going past."""

    #: Frames whose arrival is a fact about the conversation, so they must be
    #: counted once however many links they cross.
    _DEDUPED = (
        TranscriptionFrame,
        TTSTextFrame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
    )

    def __init__(self, wire: Wire) -> None:
        super().__init__()
        self._wire = wire
        self._assistant_id: str | None = None
        self._assistant_text = ""
        self._seen: OrderedDict[int, None] = OrderedDict()

    def _first_sighting(self, frame) -> bool:
        """`on_push_frame` fires on every processor-to-processor hop, so one
        frame is observed once per link it crosses.

        Phase changes are idempotent and do not care, but appending spoken text
        does: counting a `TTSTextFrame` three times because it passed TTS ->
        transport -> aggregator repeated whole sentences back into the
        patient's transcript. Pipecat stamps every frame with its own id, so
        this keys on that rather than on content — two sentences may
        legitimately be identical.
        """
        key = frame.id
        if key in self._seen:
            return False
        self._seen[key] = None
        # Bounded: a call is thousands of frames and the window only has to
        # outlive one frame's trip through the pipeline.
        if len(self._seen) > 512:
            self._seen.popitem(last=False)
        return True

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame

        if isinstance(frame, self._DEDUPED) and not self._first_sighting(frame):
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            await self._wire.phase("listening")

        elif isinstance(frame, TranscriptionFrame) and not isinstance(
            frame, InterimTranscriptionFrame
        ):
            # The committed turn, as the record will hold it.
            await self._wire.utterance("patient", frame.text, f"u_{uuid.uuid4().hex[:8]}")
            await self._wire.phase("thinking")

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._assistant_id = f"a_{uuid.uuid4().hex[:8]}"
            self._assistant_text = ""
            await self._wire.phase("thinking")

        elif isinstance(frame, TTSTextFrame):
            # Text as it is actually spoken, so the thread shows what the
            # patient heard rather than what the model wrote. The browser keys
            # bubbles by id and replaces, so re-sending grows the same bubble.
            if self._assistant_id is None:
                self._assistant_id = f"a_{uuid.uuid4().hex[:8]}"
                self._assistant_text = ""
            self._assistant_text = f"{self._assistant_text} {frame.text}".strip()
            await self._wire.utterance("assistant", self._assistant_text, self._assistant_id)

        elif isinstance(frame, BotStartedSpeakingFrame):
            await self._wire.phase("speaking")

        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._wire.phase("listening")

        elif isinstance(frame, LLMFullResponseEndFrame):
            self._assistant_id = None
