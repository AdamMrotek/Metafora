"""Orpheus pads every chunk with silence and Pipecat does not trim it.

Ported from `voice/wav.test.ts`'s trimming cases.
"""

import array
import math

from services.agent.tts import trim_silence

RATE = 24_000


def tone(ms: int, amplitude: int = 8000) -> array.array:
    n = int(RATE * ms / 1000)
    return array.array("h", [int(amplitude * math.sin(i * 0.05)) for i in range(n)])


def silence(ms: int) -> array.array:
    return array.array("h", [0] * int(RATE * ms / 1000))


def duration_ms(pcm: bytes) -> float:
    return len(pcm) / 2 / RATE * 1000


def test_trims_leading_and_trailing_padding():
    padded = silence(342) + tone(1100) + silence(882)
    out = trim_silence(padded.tobytes(), RATE)
    # 1100 ms of speech plus a 20 ms guard either side.
    assert 1100 <= duration_ms(out) <= 1100 + 2 * 20 + 5


def test_keeps_a_guard_so_a_soft_onset_is_never_clipped():
    padded = silence(300) + tone(500)
    out = trim_silence(padded.tobytes(), RATE)
    assert duration_ms(out) > 500, "the guard must survive the trim"


def test_all_silence_becomes_nothing_rather_than_a_beat_of_padding():
    assert trim_silence(silence(700).tobytes(), RATE) == b""


def test_audio_with_no_padding_is_left_alone():
    speech = tone(800)
    out = trim_silence(speech.tobytes(), RATE)
    assert duration_ms(out) == duration_ms(speech.tobytes())


def test_empty_input_is_safe():
    assert trim_silence(b"", RATE) == b""


def test_quiet_speech_below_the_threshold_is_not_mistaken_for_silence():
    """A patient trailing off is quiet, not silent — but this is the assistant's
    own audio, so the threshold only has to survive Orpheus's true zeros."""
    quiet = tone(400, amplitude=300)
    assert trim_silence(quiet.tobytes(), RATE) != b""
