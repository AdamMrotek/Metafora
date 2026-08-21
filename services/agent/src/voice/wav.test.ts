import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { decodeWav, encodeWav } from './wav.ts';

test('round-trips PCM without altering a sample', () => {
  const pcm = new Int16Array(4096);
  for (let i = 0; i < pcm.length; i++) pcm[i] = Math.round(Math.sin(i / 12) * 32767);

  const { pcm: back, sampleRate } = decodeWav(encodeWav(pcm, 16_000));
  assert.equal(sampleRate, 16_000);
  assert.deepEqual(Array.from(back), Array.from(pcm));
});

test('preserves the extremes of the range', () => {
  const pcm = Int16Array.from([0, 1, -1, 32767, -32768]);
  assert.deepEqual(Array.from(decodeWav(encodeWav(pcm, 48_000)).pcm), Array.from(pcm));
});

test('reads a WAV written by another encoder', () => {
  const file = resolve(dirname(fileURLToPath(import.meta.url)), 'fixtures/speech-16k.wav');
  const { pcm, sampleRate } = decodeWav(readFileSync(file));
  assert.equal(sampleRate, 16_000);
  assert.ok(pcm.length > 16_000, 'fixture should be over a second of audio');
});

test('finds the data chunk even when other chunks precede it', () => {
  const pcm = Int16Array.from([5, -5, 100, -100]);
  const canonical = encodeWav(pcm, 24_000);

  // Splice a LIST chunk between `fmt ` and `data`, as real encoders do.
  const list = Buffer.alloc(8 + 10);
  list.write('LIST', 0, 'ascii');
  list.writeUInt32LE(10, 4);
  const spliced = Buffer.concat([canonical.subarray(0, 36), list, canonical.subarray(36)]);
  spliced.writeUInt32LE(spliced.length - 8, 4);

  const { pcm: back, sampleRate } = decodeWav(spliced);
  assert.equal(sampleRate, 24_000);
  assert.deepEqual(Array.from(back), Array.from(pcm));
});

test('refuses a file that is not a WAV', () => {
  assert.throws(() => decodeWav(Buffer.from('this is not audio at all')), /RIFF/);
});
