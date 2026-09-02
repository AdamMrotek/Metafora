"""The session log, written from inside the media path.

Because every transcript and every reply passes through this process, the log
is written *from the source* rather than reconstructed from whatever the browser
chose to relay. That is the audit trail: both sides of the conversation, every
state transition, every red-flag decision, and the latency of every turn.

`loop.ts` could write all of this inline because it was the only thing that saw
every step. In a pipeline nothing has that view except an observer, so this is
where the record is kept. Pipecat's own OTel spans and metrics frames are
telemetry *about the system*; they are not this artefact, and a metrics story
should not be allowed to replace it.

One clock per turn, because the five latency numbers are all differences
against it — and the one that matters starts when the patient stopped talking,
not when we noticed.
"""

import time

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from services.agent.config.tuning import ENDPOINT_SILENCE_MS
from services.agent.session_log import (
    EndpointDecision,
    LatencyTurn,
    LlmCompleted,
    SessionWriter,
    TtsSpoken,
    TurnAborted,
    TurnCommitted,
)


class SessionLogObserver(BaseObserver):
    """Derives the conversation record from frames going past."""

    _DEDUPED = (
        TranscriptionFrame,
        TTSTextFrame,
        LLMTextFrame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
        FunctionCallResultFrame,
    )

    def __init__(
        self,
        writer: SessionWriter,
        *,
        floor_ms: float = ENDPOINT_SILENCE_MS,
    ) -> None:
        super().__init__()
        self._writer = writer
        self._floor_ms = floor_ms
        self._seen: dict[int, None] = {}
        self._reset_turn()

    def _reset_turn(self) -> None:
        # The clock starts when the patient stopped talking, not when we noticed.
        self._t0: float | None = None
        self._t_commit: float | None = None
        self._t_stt: float | None = None
        self._t_first_token: float | None = None
        self._t_first_sound: float | None = None
        self._reply = ""
        self._tool_call_ids: set[str] = set()
        self._tts_chars = 0
        self._tts_chunks = 0
        # System frames are broadcast to every branch and cross many links, so
        # "once per turn" is a guard, not a dedupe.
        self._logged_latency = False
        self._logged_endpoint = False
        self._logged_llm = False
        self._logged_tts = False

    def _first_sighting(self, frame) -> bool:
        """One frame is observed once per link it crosses; a record must count
        it once."""
        if frame.id in self._seen:
            return False
        self._seen[frame.id] = None
        if len(self._seen) > 1024:
            self._seen.pop(next(iter(self._seen)))
        return True

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        now = time.monotonic()

        if isinstance(frame, UserStartedSpeakingFrame):
            self._reset_turn()
            return

        if isinstance(frame, self._DEDUPED) and not self._first_sighting(frame):
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # VAD confirms silence only after `stop_secs` has elapsed, so wind
            # the clock back to when the patient actually stopped.
            if self._t0 is None:
                self._t0 = now - getattr(frame, "stop_secs", 0.0)

        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self._t_commit is None:
                self._t_commit = now
            self._log_endpoint_decision(now)

        elif isinstance(frame, TranscriptionFrame) and not isinstance(
            frame, InterimTranscriptionFrame
        ):
            self._t_stt = now
            base = self._t0 or self._t_commit or now
            self._writer.append(
                TurnCommitted(
                    transcript=frame.text,
                    durationMs=round((now - base) * 1000, 1),
                    source="typed" if frame.user_id == "patient-typed" else "voice",
                )
            )

        elif isinstance(frame, LLMTextFrame):
            if self._t_first_token is None:
                self._t_first_token = now
            self._reply += frame.text

        elif isinstance(frame, FunctionCallResultFrame):
            # A result is broadcast both ways, so count the call, not the
            # sighting.
            self._tool_call_ids.add(frame.tool_call_id or str(frame.id))

        elif isinstance(frame, TTSTextFrame):
            self._tts_chars += len(frame.text)
            self._tts_chunks += 1

        elif isinstance(frame, TTSAudioRawFrame):
            if self._t_first_sound is None:
                # First *sound*, so this is taken when playback begins.
                self._t_first_sound = now
                self._log_latency()

        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._logged_llm:
                return
            if self._reply.strip() or self._tool_call_ids:
                self._logged_llm = True
                self._writer.append(
                    LlmCompleted(
                        text=self._reply.strip(), toolCalls=len(self._tool_call_ids)
                    )
                )

        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self._tts_chunks and not self._logged_tts:
                self._logged_tts = True
                self._writer.append(
                    TtsSpoken(chars=self._tts_chars, chunks=self._tts_chunks)
                )

        elif isinstance(frame, InterruptionFrame):
            # An aborted reply that stays in the record corrupts it, not just
            # the experience — so what was cut off is recorded as discarded.
            if self._reply.strip():
                self._writer.append(TurnAborted(discardedText=self._reply.strip()))
            self._reset_turn()

    def _log_endpoint_decision(self, now: float) -> None:
        if self._t0 is None or self._logged_endpoint:
            return
        self._logged_endpoint = True
        silence_ms = (now - self._t0) * 1000
        self._writer.append(
            EndpointDecision(
                silenceMs=round(silence_ms, 1),
                decidedBy="smart_turn_v3",
                floorMs=self._floor_ms,
                floorWouldHaveWaited=silence_ms < self._floor_ms,
            )
        )

    def _log_latency(self) -> None:
        """The five numbers, all differences against one clock."""
        # The opening is spoken before any turn exists; it has no latency to
        # report, and five values of -1 in the record are worse than no row.
        if self._logged_latency or self._t_first_sound is None or self._t0 is None:
            return
        self._logged_latency = True

        ms: dict[str, float] = {}

        def diff(a: float | None, b: float | None) -> float:
            return round((a - b) * 1000, 1) if a is not None and b is not None else -1.0

        # The three stages the patient actually waits through, measured against
        # one clock so that they sum to `perceived_first_sound`.
        #
        # `loop.ts` measured the LLM from the moment STT returned, because the
        # turn was committed first and transcribed second. Pipecat runs STT
        # *concurrently* with turn detection, so the transcript is usually ready
        # before the turn is declared over and that subtraction went negative.
        # The stages are therefore anchored on the commit.
        #
        # Silence the patient sat through before we called the turn over. A
        # design choice, but it is on their clock, so it is counted.
        ms["endpoint_wait"] = diff(self._t_commit, self._t0)
        ms["llm_first_token"] = diff(self._t_first_token, self._t_commit)
        ms["tts_first_audio"] = diff(self._t_first_sound, self._t_first_token)
        # The number that actually matters: patient stops talking → hears a voice.
        ms["perceived_first_sound"] = diff(self._t_first_sound, self._t0)
        # Informational: when the transcript was ready, on the patient's clock.
        # Negative against `endpoint_wait` means STT finished before the turn
        # was called — i.e. it is off the critical path, which is where we want
        # it.
        ms["stt_ready"] = diff(self._t_stt, self._t0)

        self._writer.append(LatencyTurn(ms=ms))
