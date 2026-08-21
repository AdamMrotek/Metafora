import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as ort from 'onnxruntime-node';
import {
  FRAME_SAMPLES,
  FRAME_SIZE_MS,
  SAMPLE_RATE,
  TUNING,
  VAD_CONTEXT_SAMPLES,
} from '../config/tuning.ts';

/**
 * Endpointing — "is this silence a pause, or a finished turn?"
 *
 * This is the single hardest quality problem in the system and the one most
 * likely to make the assistant feel bad to use. It is also clinical behaviour:
 * when a patient trails off mid-sentence describing a headache, what happens
 * next is a decision we own and must be able to explain, which is why it is
 * written here rather than inherited from a framework tuned for consumer
 * voice agents.
 *
 * Silero VAD v6 (MIT) scores each 32 ms frame. Everything above that score is
 * our policy, and all of it is in `config/tuning.ts`.
 */

const MODEL_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../vendor/silero_vad.onnx',
);

export type VadEvent =
  /** Speech confirmed. The assistant should stop expecting to talk. */
  | { type: 'speech_start' }
  /** Speech held long enough to count as a real interruption, not echo. */
  | { type: 'speech_sustained' }
  /** A committed turn: everything the patient said, pre-roll included. */
  | {
      type: 'utterance';
      pcm: Int16Array;
      durationMs: number;
      reason: 'endpoint' | 'max_length';
      /**
       * When the patient actually stopped talking — not when we decided they
       * had. Latency has to be measured from here, because the endpoint wait
       * is silence the patient is already sitting through.
       */
      speechEndedAt: number;
    }
  /** Speech too short to be a turn — a cough, a door, a hum. */
  | { type: 'discarded'; durationMs: number };

export class Endpointer {
  private state: Float32Array;
  private context: Float32Array;
  private mode: 'silence' | 'speaking' = 'silence';

  /** Frames held before speech is confirmed, so the first word survives. */
  private preroll: Int16Array[] = [];
  private utterance: Int16Array[] = [];

  private speechRun = 0;
  private trailingSilence = 0;
  private speechFrames = 0;

  private constructor(private readonly session: ort.InferenceSession) {
    this.state = new Float32Array(2 * 1 * 128);
    this.context = new Float32Array(VAD_CONTEXT_SAMPLES);
  }

  static async create(): Promise<Endpointer> {
    const session = await ort.InferenceSession.create(MODEL_PATH);
    return new Endpointer(session);
  }

  /** Score one 32 ms frame and return whatever it decided. */
  async push(frame: Int16Array): Promise<VadEvent[]> {
    if (frame.length !== FRAME_SAMPLES) {
      throw new Error(`expected ${FRAME_SAMPLES} samples, got ${frame.length}`);
    }
    const probability = await this.infer(frame);
    const isSpeech = probability > TUNING.speechProbability;
    const events: VadEvent[] = [];

    this.speechRun = isSpeech ? this.speechRun + 1 : 0;

    // A sustained run of speech is what distinguishes a deliberate
    // interruption from the assistant's own voice leaking back through AEC.
    // It can legitimately re-fire within a turn — each new run is a fresh
    // chance to interrupt — so it is not gated on the mode.
    if (this.speechRun === TUNING.bargeInFrames) events.push({ type: 'speech_sustained' });

    if (this.mode === 'silence') {
      this.preroll.push(frame);
      if (this.preroll.length > TUNING.prerollFrames) this.preroll.shift();

      if (this.speechRun >= TUNING.speechStartFrames) {
        this.mode = 'speaking';
        // Onset fires once per turn, on the transition. A pause mid-answer
        // resets the run counter, and re-announcing the start of a turn that
        // is already underway would make the call bar flicker.
        events.push({ type: 'speech_start' });
        // The pre-roll already holds the frames that confirmed onset, plus the
        // half second before them.
        this.utterance = this.preroll.slice();
        this.preroll = [];
        this.speechFrames = this.speechRun;
        this.trailingSilence = 0;
      }
      return events;
    }

    this.utterance.push(frame);
    if (isSpeech) {
      this.speechFrames++;
      this.trailingSilence = 0;
    } else {
      this.trailingSilence++;
    }

    if (this.trailingSilence >= TUNING.endpointSilenceFrames) {
      events.push(this.commit('endpoint'));
    } else if (this.utterance.length >= TUNING.maxUtteranceFrames) {
      events.push(this.commit('max_length'));
    }
    return events;
  }

  /** Close the turn and hand back the audio. */
  private commit(reason: 'endpoint' | 'max_length'): VadEvent {
    // Drop the dead air we waited through, but leave a short tail so a quiet
    // final consonant is not clipped off the last word.
    const keepTail = Math.min(this.trailingSilence, TUNING.speechStartFrames);
    const frames = this.utterance.slice(0, this.utterance.length - this.trailingSilence + keepTail);

    const speechMs = this.speechFrames * FRAME_SIZE_MS;
    const trailingSilenceMs = this.trailingSilence * FRAME_SIZE_MS;
    this.resetTurn();

    if (this.speechFramesTooShort(speechMs)) {
      return { type: 'discarded', durationMs: speechMs };
    }
    return {
      type: 'utterance',
      pcm: concat(frames),
      durationMs: frames.length * FRAME_SIZE_MS,
      reason,
      speechEndedAt: Date.now() - trailingSilenceMs,
    };
  }

  private speechFramesTooShort(speechMs: number): boolean {
    return speechMs < TUNING.minUtteranceFrames * FRAME_SIZE_MS;
  }

  private resetTurn() {
    this.mode = 'silence';
    this.utterance = [];
    this.preroll = [];
    this.speechRun = 0;
    this.trailingSilence = 0;
    this.speechFrames = 0;
  }

  /** Forget the turn in progress without emitting it. */
  reset() {
    this.resetTurn();
  }

  private async infer(frame: Int16Array): Promise<number> {
    // Silero takes 64 samples of carried context followed by the new frame.
    const input = new Float32Array(VAD_CONTEXT_SAMPLES + FRAME_SAMPLES);
    input.set(this.context, 0);
    for (let i = 0; i < frame.length; i++) {
      input[VAD_CONTEXT_SAMPLES + i] = frame[i]! / 32768;
    }

    const out = await this.session.run({
      input: new ort.Tensor('float32', input, [1, input.length]),
      state: new ort.Tensor('float32', this.state, [2, 1, 128]),
      sr: new ort.Tensor('int64', BigInt64Array.from([BigInt(SAMPLE_RATE)]), []),
    });

    this.state = out.stateN!.data as Float32Array;
    this.context = input.slice(input.length - VAD_CONTEXT_SAMPLES);
    return (out.output!.data as Float32Array)[0]!;
  }
}

function concat(frames: Int16Array[]): Int16Array {
  const total = frames.reduce((n, f) => n + f.length, 0);
  const out = new Int16Array(total);
  let at = 0;
  for (const f of frames) {
    out.set(f, at);
    at += f.length;
  }
  return out;
}
