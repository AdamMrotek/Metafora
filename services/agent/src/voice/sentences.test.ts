import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { TTS_MAX_CHARS } from '../config/tuning.ts';
import { SentenceChunker } from './sentences.ts';

/** Feed a whole reply one character at a time, as a stream would. */
function streamAll(text: string): string[] {
  const c = new SentenceChunker();
  const out: string[] = [];
  for (const ch of text) out.push(...c.push(ch));
  out.push(...c.flush());
  return out;
}

test('splits on sentence terminators', () => {
  assert.deepEqual(streamAll('Hello Alice. How is your day going?'), [
    'Hello Alice.',
    'How is your day going?',
  ]);
});

test('emits the first sentence before the reply is finished', () => {
  const c = new SentenceChunker();
  const first = c.push('Hello Alice. And then');
  assert.deepEqual(first, ['Hello Alice.'], 'sentence one must not wait for the rest');
});

test('does not split a decimal number', () => {
  assert.deepEqual(streamAll('You take 5.5 mg each morning.'), ['You take 5.5 mg each morning.']);
});

test('does not split on a title abbreviation', () => {
  assert.deepEqual(streamAll('Dr. Hollis asked me to call.'), ['Dr. Hollis asked me to call.']);
});

test('keeps closing punctuation with its sentence', () => {
  assert.deepEqual(streamAll('She said "yes." Then she left.'), [
    'She said "yes."',
    'Then she left.',
  ]);
});

test('never exceeds the Orpheus cap', () => {
  const long = `${'a solid clause of prose '.repeat(40)}.`;
  for (const chunk of streamAll(long)) {
    assert.ok(chunk.length <= TTS_MAX_CHARS, `chunk was ${chunk.length} chars`);
  }
});

test('never cuts mid-word', () => {
  const long = 'ramipril amlodipine bisoprolol atorvastatin levothyroxine '.repeat(8);
  const chunks = streamAll(long);
  const rejoined = chunks.join(' ').split(/\s+/).filter(Boolean);
  assert.deepEqual(rejoined, long.split(/\s+/).filter(Boolean), 'words must survive intact');
});

test('flushes an unterminated tail', () => {
  assert.deepEqual(streamAll('no full stop here'), ['no full stop here']);
});

test('handles an empty stream', () => {
  assert.deepEqual(streamAll(''), []);
});

test('packs later sentences to spend fewer TTS requests', () => {
  // Every chunk is one request, and the provider limits requests, not
  // characters. Only the first chunk is on the latency path.
  const reply =
    'Hello Alice. Dr Hollis asked me to have a quick chat with you before your appointment. ' +
    'Just talk normally, and jump in whenever you like. How is your day going?';

  const packed = streamAll(reply);
  const unpacked = (() => {
    const c = new SentenceChunker(false);
    const out: string[] = [];
    for (const ch of reply) out.push(...c.push(ch));
    out.push(...c.flush());
    return out;
  })();

  assert.equal(unpacked.length, 4, 'one request per sentence, unpacked');
  assert.ok(packed.length < unpacked.length, `packing saved nothing: ${packed.length}`);
  assert.ok(packed.every((c) => c.length <= TTS_MAX_CHARS));

  // The first sentence still goes out alone, so first sound is unaffected.
  assert.equal(packed[0], 'Hello Alice.');

  // No words are lost or reordered by packing.
  assert.deepEqual(packed.join(' ').split(/\s+/), reply.split(/\s+/));
});

test('the first sentence is never held back for packing', () => {
  const c = new SentenceChunker();
  assert.deepEqual(c.push('Hello Alice. And then some more text follows'), ['Hello Alice.']);
});

test('does not split a name at an initial', () => {
  // "Dr E. Hollis" split into "Dr E." and "Hollis..." puts an audible seam
  // through the middle of the clinician's name.
  assert.deepEqual(streamAll('Dr E. Hollis asked me to call you.'), [
    'Dr E. Hollis asked me to call you.',
  ]);
  assert.deepEqual(streamAll('Ask J. R. Smith about it. Then call me.'), [
    'Ask J. R. Smith about it.',
    'Then call me.',
  ]);
});
