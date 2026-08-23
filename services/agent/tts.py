"""Groq TTS with Orpheus's silence padding removed.

Pipecat's `GroqTTSService` is correct about the sample rate by omission — it
sends no `sample_rate`, so the WAV header comes back honest at 24 kHz and
`base_output` resamples up to the room's rate. Do not "fix" that by passing the
room's rate: `sample_rate` on the speech endpoint only rewrites the header, and
asking for 48 kHz relabels 24 kHz audio, which is what made the assistant speak
at double speed. Verified 2026-08-22: identical PCM for 24000, 48000 and the
parameter omitted.

What Pipecat does *not* do is trim the padding, which is what this subclass is
for.
"""

import array
from collections.abc import AsyncGenerator

from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.services.groq.tts import GroqTTSService

from services.agent.config.tuning import TTS_SILENCE_GUARD_MS, TTS_SILENCE_THRESHOLD


def trim_silence(
    pcm: bytes,
    sample_rate: int,
    threshold: int = TTS_SILENCE_THRESHOLD,
    guard_ms: int = TTS_SILENCE_GUARD_MS,
) -> bytes:
    """Trim the silence Orpheus pads onto every chunk.

    Measured 2026-08-22: "How is your day going?" returned 1.10 s of speech
    inside 342 ms of leading and 882 ms of trailing silence — 53% of the audio.
    Chunks are spoken back to back with a deliberate beat between them, so that
    padding stacks on top of `SENTENCE_PAUSE_MS` and turns every full stop into
    over a second of dead air. The pause the patient hears should be the one in
    `tuning.py` and nothing else; trimming here is what makes that constant mean
    what it says. The leading pad is worse — it is pure latency on every
    sentence the patient is waiting for.

    A short guard is kept at each end so a soft consonant onset is never clipped.
    """
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return pcm

    guard = round(sample_rate * guard_ms / 1000)

    start = 0
    while start < len(samples) and abs(samples[start]) < threshold:
        start += 1
    if start == len(samples):
        return b""  # nothing but silence

    end = len(samples)
    while end > start and abs(samples[end - 1]) < threshold:
        end -= 1

    return samples[max(0, start - guard) : min(len(samples), end + guard)].tobytes()


class TrimmedGroqTTSService(GroqTTSService):
    """`GroqTTSService` with Orpheus's leading and trailing silence removed.

    The audio is buffered before trimming rather than trimmed per frame: the
    leading pad belongs to the first frame and the trailing pad to the last, and
    which frame is last is not known until the generator is done. Orpheus
    returns one complete WAV per request and our chunks are capped at 200
    characters, so there is nothing to stream through in the meantime.
    """

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        audio = bytearray()
        rate: int | None = None
        channels = 1

        async for frame in super().run_tts(text, context_id):
            if isinstance(frame, TTSAudioRawFrame):
                audio.extend(frame.audio)
                rate = frame.sample_rate
                channels = frame.num_channels
            else:
                # ErrorFrame and friends pass straight through.
                yield frame

        if not audio or rate is None:
            return

        trimmed = trim_silence(bytes(audio), rate)
        if trimmed:
            yield TTSAudioRawFrame(trimmed, rate, channels, context_id=context_id)
