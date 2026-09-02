"""Endpointing and barge-in constants.

These are clinical behaviour, not incidental infrastructure. When a patient
trails off mid-sentence describing a headache, what the system does next is
a decision we own and must be able to explain. They live in one file so that
tuning them is a deliberate, reviewable act rather than a hunt through the
audio loop.

Since the Pipecat migration most of these are handed to the framework rather
than executed by our own loop — `SileroVADAnalyzer` takes seconds, not frame
counts — so the values are expressed in both units. The *arguments* below are
what survives; the frame arithmetic was an artefact of `vad.ts`.
"""

# ─── Audio format ────────────────────────────────────────────────────────────
# Was a contract with `svc-media`, which no longer exists — Pipecat's LiveKit
# transport owns the frame format now. Kept because the safety of the
# endpointing numbers depends on knowing what they were tuned against.

SAMPLE_RATE = 16_000
"""Silero VAD wants exactly 512 samples at 16 kHz. 512 / 16000 = 32 ms."""

FRAME_SIZE_MS = 32
FRAME_SAMPLES = 512

OUTPUT_SAMPLE_RATE = 48_000
"""The rate the room runs at. Pipecat resamples TTS output up to this."""


# ─── Endpointing ─────────────────────────────────────────────────────────────

SPEECH_PROBABILITY = 0.5
"""Above this, Silero calls the frame speech. -> VADParams.confidence"""

SPEECH_START_MS = 250
"""Sustained speech before we believe the patient has started.

-> VADParams.start_secs
"""

ENDPOINT_SILENCE_MS = 700
"""Silence before we commit the turn. -> VADParams.stop_secs

The architecture note suggested 300-500 ms; we start longer. A fixed short
threshold is infuriating for a patient describing symptoms haltingly, or
elderly, or in pain, and the cost of waiting is a beat of latency while the
cost of cutting in is a half-finished answer the patient has to repeat.

**Pipecat 1.7 does not use this to end turns at all.** The user-turn stop
decision belongs to `LocalSmartTurnAnalyzerV3`, a model that judges whether the
speaker finished a thought. That may well be better for the patient this number
was written for — it can wait longer than 700 ms when someone sounds mid-thought
— but it is not explainable the way a constant is.

Decision, 22 Aug 2026: run the framework's turn detection, and log what the
700 ms floor would have done on every turn (`EndpointDecision`), so the choice
is settled by real calls rather than by argument. This number is therefore a
*measurement baseline* today, not the live threshold. If it ever becomes the
live threshold again, that is `SpeechTimeoutUserTurnStopStrategy(
user_speech_timeout=0.7)` — whose own docs call it a "policy floor".
"""

MAX_UTTERANCE_MS = 30_000
"""A turn cannot run forever. Force-commit and let the pipeline catch up."""

MIN_UTTERANCE_MS = 300
"""Ignore blips: a cough is not a turn."""

PREROLL_MS = 500
"""Retained before speech onset so the first word is never clipped.

VAD confirms speech ~250 ms after it actually began; without pre-roll that
quarter second is simply lost. Pipecat's VAD buffers internally; this number
is retained as the acceptance criterion, not as a setting.
"""

BARGE_IN_MS = 300
"""Barge-in needs *sustained* speech, not one hot frame.

The assistant's voice comes out of the patient's speaker and back into their
mic; browser AEC removes most of it, and this threshold absorbs what leaks
through. Set it too low and the assistant interrupts itself.
"""


# ─── Pacing ──────────────────────────────────────────────────────────────────

SENTENCE_PAUSE_MS = 260
"""A beat of silence between spoken sentences.

This is the only working rate control we have. Groq's Orpheus accepts a
`speed` parameter and *silently ignores it* — 0.6, 1.0 and 1.8 all return
byte-identical audio — and its `[slowly]` vocal-direction cue is worse
than useless, producing shorter audio than no cue at all. Punctuation
does affect pacing, but rewriting a clinician-facing sentence to slow it
down is not a trade worth making.

Inserting silence between chunks is deterministic, costs no quality, and
does not touch a single word. Raise it to make the assistant feel less
hurried; 0 disables it.

Corroborated independently by Pipecat's own `GroqTTSService`, which notes
that as of 2026-02-25 only a speed of 1.0 is supported.
"""

QUESTION_PAUSE_MS = 450
"""A longer beat after a question, so the patient knows it is their turn."""


# ─── Inference ───────────────────────────────────────────────────────────────

STT_MODEL = "whisper-large-v3-turbo"
LLM_MODEL = "openai/gpt-oss-120b"
TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "hannah"
"""Groq's default is `autumn`; this must be set explicitly."""

TTS_MAX_CHARS = 200
"""Orpheus rejects anything longer. Drives the sentence chunker."""

TTS_NATIVE_SAMPLE_RATE = 24_000
"""The rate Orpheus actually generates at.

`sample_rate` on the speech endpoint does not resample — it is written into
the WAV header and nothing else. Asking for 48 kHz returns byte-identical
PCM to asking for 24 kHz, with only the header field changed, so a request
for the room's rate produced 24 kHz audio labelled 48 kHz and the assistant
spoke at double speed. Verified by requesting one sentence at both rates:
191,992 bytes either way, differing in 5 header bytes.

Re-verified against the live API on 2026-08-22 at 280,390 bytes: identical
PCM (sha 8e63ee7d…) for 24000, 48000 and the parameter omitted entirely.
Pipecat's `GroqTTSService` omits it, so the header comes back honest and
`base_output.py` resamples to `OUTPUT_SAMPLE_RATE`. Do not "fix" it by
passing the room's rate.
"""

TTS_SILENCE_THRESHOLD = 250
"""Amplitude below which a sample counts as silence, for trimming Orpheus's padding.

Orpheus wraps every chunk in silence and Pipecat does not trim it. Measured
2026-08-22: "How is your day going?" returned 1.10 s of speech inside 342 ms
of leading and 882 ms of trailing silence — 53% of the audio. The trailing pad
stacks on `QUESTION_PAUSE_MS`; the leading pad is pure latency on every
sentence the patient waits for.
"""

TTS_SILENCE_GUARD_MS = 20
"""Kept either side of the trim, so a soft onset is never clipped."""


CONCERN_HOLD_MS = 1500
"""How long the assistant's reply waits for the capture pass, on a question that
can stop the call.

Both passes run on the same turn, so which of them finishes first is a race, and
on exactly one kind of question the answer matters: one whose `QuestionFlag` set
contains an `end_call`. There, the speech pass is answering as though the
interview continues while the capture pass is deciding that it does not, and
whichever lands first is what the patient hears.

Measured on real calls (`transcript.events`, ms from the committed turn to each
pass finishing):

    speech  1938   capture  1471      capture first — nothing is heard
    speech  3451   capture  3490      even
    speech  2091   capture  2188      even
    speech  5536   capture 13926      capture 8.4 s behind — a sentence got out

So the passes normally finish within ~700 ms of each other and the hold is free;
the fourth is the one this exists for. `iv_5abb66a97374` is that call: the
patient said "No, no, no, no." and heard "I hear you can't make it." before the
closure, because the first sentence had already been synthesised by the time the
concern was known.

1.5 s covers the measured spread with room, and is short enough to be an
ordinary pause if it is ever spent in full. It is not a fix for the outlier —
nothing here can be, because that pass was eight seconds late — it is the bound
on how long the patient waits before the assistant speaks anyway. Past it the
reply is released and the call behaves as it did: the concern still stops it,
one sentence later.

Only questions that can stop the call are held. Every other turn is untouched,
because on those there is nothing the capture pass could say that the speech
pass needs to wait for.
"""
