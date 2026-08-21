import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { FRAME_SAMPLES, FRAME_SIZE_MS, TUNING } from '../config/tuning.ts';
import { Endpointer, type VadEvent } from './vad.ts';
import { decodeWav } from './wav.ts';

/**
 * Fixture is real speech, generated with macOS `say` at 16 kHz mono:
 *   say -o speech-16k.wav --data-format=LEI16@16000 "Hello there. My day is
 *   going quite well, thank you for asking."
 *
 * Synthetic tones and noise will not do — Silero correctly scores them near
 * zero, so a test built on them proves only that the model rejects non-speech.
 */
const FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), 'fixtures/speech-16k.wav');

const SILENT = new Int16Array(FRAME_SAMPLES);

function framesOf(pcm: Int16Array): Int16Array[] {
  const out: Int16Array[] = [];
  for (let o = 0; o + FRAME_SAMPLES <= pcm.length; o += FRAME_SAMPLES) {
    out.push(pcm.subarray(o, o + FRAME_SAMPLES));
  }
  return out;
}

const silence = (n: number) => Array.from({ length: n }, () => SILENT);

async function run(seq: Int16Array[]): Promise<VadEvent[]> {
  const ep = await Endpointer.create();
  const events: VadEvent[] = [];
  for (const f of seq) events.push(...(await ep.push(f)));
  return events;
}

test('silence alone never commits a turn', async () => {
  assert.deepEqual(await run(silence(120)), []);
});

test('real speech produces exactly one onset and one committed turn', async () => {
  const { pcm } = decodeWav(readFileSync(FIXTURE));
  const events = await run([...silence(10), ...framesOf(pcm), ...silence(40)]);

  assert.equal(
    events.filter((e) => e.type === 'speech_start').length,
    1,
    'onset must fire once per turn, not on every pause inside it',
  );

  const committed = events.filter((e) => e.type === 'utterance');
  assert.equal(committed.length, 1, 'a single sentence pair is one turn, not two');
  assert.equal((committed[0] as any).reason, 'endpoint');
});

test('a pause mid-answer does not split the turn', async () => {
  const { pcm } = decodeWav(readFileSync(FIXTURE));
  const half = Math.floor(framesOf(pcm).length / 2);
  const all = framesOf(pcm);

  // 400 ms of thinking silence, comfortably under the 700 ms endpoint.
  const gap = silence(Math.round(400 / FRAME_SIZE_MS));
  const events = await run([
    ...silence(10),
    ...all.slice(0, half),
    ...gap,
    ...all.slice(half),
    ...silence(40),
  ]);

  assert.equal(
    events.filter((e) => e.type === 'utterance').length,
    1,
    'pausing to think must not submit half an answer',
  );
});

test('the committed turn includes pre-roll, so the first word survives', async () => {
  const { pcm } = decodeWav(readFileSync(FIXTURE));
  const events = await run([...silence(20), ...framesOf(pcm), ...silence(40)]);
  const utterance = events.find((e) => e.type === 'utterance') as Extract<
    VadEvent,
    { type: 'utterance' }
  >;

  // Onset is only confirmed 250 ms in; without pre-roll that audio is lost.
  const speechMs = (pcm.length / 16_000) * 1000;
  assert.ok(
    utterance.durationMs > speechMs,
    `turn was ${utterance.durationMs}ms but speech alone was ${Math.round(speechMs)}ms`,
  );
});

test('barge-in signal needs sustained speech, not one hot frame', async () => {
  const { pcm } = decodeWav(readFileSync(FIXTURE));
  const oneFrame = framesOf(pcm).slice(0, 1);
  assert.deepEqual(await run([...silence(5), ...oneFrame, ...silence(30)]), []);

  const events = await run([...silence(5), ...framesOf(pcm), ...silence(40)]);
  assert.ok(events.some((e) => e.type === 'speech_sustained'));
});

test('a brief blip is discarded rather than transcribed', async () => {
  const { pcm } = decodeWav(readFileSync(FIXTURE));
  // Just past the onset threshold, but well under the minimum turn length.
  const blip = framesOf(pcm).slice(0, TUNING.speechStartFrames + 2);
  const events = await run([...silence(5), ...blip, ...silence(40)]);

  assert.equal(events.filter((e) => e.type === 'utterance').length, 0);
  assert.ok(events.some((e) => e.type === 'discarded'), 'a cough is not a turn');
});

test('reset abandons the turn in progress without emitting it', async () => {
  const { pcm } = decodeWav(readFileSync(FIXTURE));
  const ep = await Endpointer.create();
  const events: VadEvent[] = [];

  for (const f of [...silence(5), ...framesOf(pcm).slice(0, 30)]) events.push(...(await ep.push(f)));
  ep.reset();
  for (const f of silence(40)) events.push(...(await ep.push(f)));

  assert.equal(events.filter((e) => e.type === 'utterance').length, 0);
});

test('rejects a frame that is not exactly one VAD window', async () => {
  const ep = await Endpointer.create();
  await assert.rejects(() => ep.push(new Int16Array(256)), /expected 512 samples/);
});
