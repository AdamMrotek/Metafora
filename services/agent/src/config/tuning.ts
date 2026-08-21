/**
 * Endpointing and barge-in constants.
 *
 * These are clinical behaviour, not incidental infrastructure. When a patient
 * trails off mid-sentence describing a headache, what the system does next is
 * a decision we own and must be able to explain. They live in one file so that
 * tuning them is a deliberate, reviewable act rather than a hunt through the
 * audio loop.
 */

/**
 * The frame format itself is a contract with `svc-media`, so it lives in
 * `@metafora/contracts`. Re-exported here because every caller in this service
 * reaches for the timings and the format together.
 */
import { FRAME_SIZE_MS } from '@metafora/contracts';

export {
  FRAME_SAMPLES,
  FRAME_SIZE_MS,
  OUTPUT_SAMPLE_RATE,
  SAMPLE_RATE,
} from '@metafora/contracts';

/** Silero v6 prepends 64 samples of context to each frame. */
export const VAD_CONTEXT_SAMPLES = 64;

const frames = (ms: number) => Math.round(ms / FRAME_SIZE_MS);

export const TUNING = {
  /** Above this, Silero calls the frame speech. */
  speechProbability: 0.5,

  /** Sustained speech before we believe the patient has started. */
  speechStartFrames: frames(250),

  /**
   * Silence before we commit the turn. The architecture note suggested
   * 300–500 ms; we start longer. A fixed short threshold is infuriating for a
   * patient describing symptoms haltingly, or elderly, or in pain, and the
   * cost of waiting is a beat of latency while the cost of cutting in is a
   * half-finished answer the patient has to repeat.
   */
  endpointSilenceFrames: frames(700),

  /** A turn cannot run forever. Force-commit and let the loop catch up. */
  maxUtteranceFrames: frames(30_000),

  /** Ignore blips: a cough is not a turn. */
  minUtteranceFrames: frames(300),

  /**
   * Retained before speech onset so the first word is never clipped. VAD
   * confirms speech ~250 ms after it actually began; without pre-roll that
   * quarter second is simply lost.
   */
  prerollFrames: frames(500),

  /**
   * Barge-in needs *sustained* speech, not one hot frame. The assistant's
   * voice comes out of the patient's speaker and back into their mic; browser
   * AEC removes most of it, and this threshold absorbs what leaks through.
   * Set it too low and the assistant interrupts itself.
   */
  bargeInFrames: frames(300),
  /**
   * A beat of silence between spoken sentences.
   *
   * This is the only working rate control we have. Groq's Orpheus accepts a
   * `speed` parameter and *silently ignores it* — 0.6, 1.0 and 1.8 all return
   * byte-identical audio — and its `[slowly]` vocal-direction cue is worse
   * than useless, producing shorter audio than no cue at all. Punctuation
   * does affect pacing, but rewriting a clinician-facing sentence to slow it
   * down is not a trade worth making.
   *
   * Inserting silence between chunks is deterministic, costs no quality, and
   * does not touch a single word. Raise it to make the assistant feel less
   * hurried; 0 disables it.
   */
  sentencePauseMs: 260,

  /** A longer beat after a question, so the patient knows it is their turn. */
  questionPauseMs: 450,
} as const;

/** Inference. Kept here so the model choice is visible next to the timings. */
export const MODELS = {
  stt: 'whisper-large-v3-turbo',
  llm: 'openai/gpt-oss-120b',
  tts: 'canopylabs/orpheus-v1-english',
  ttsVoice: 'hannah',
} as const;

/** Orpheus rejects anything longer. Drives the sentence chunker. */
export const TTS_MAX_CHARS = 200;
