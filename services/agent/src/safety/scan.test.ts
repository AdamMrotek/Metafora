import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { WARMUP_V1 } from '../config/protocol.ts';
import { scan } from './scan.ts';

/**
 * The studio spec requires one fixture per catalog entry, generated from the
 * utterance that proves the flag fires. This test derives itself from the
 * protocol, so a flag added without a proving utterance fails here rather than
 * shipping unproven.
 */
test('every red flag fires on its own proving utterance', () => {
  assert.ok(WARMUP_V1.redFlags.length > 0, 'the catalog must not be empty');

  for (const flag of WARMUP_V1.redFlags) {
    assert.ok(flag.provingUtterance, `${flag.id} has no proving utterance`);
    const result = scan(flag.provingUtterance, WARMUP_V1);
    assert.ok(
      result.hits.some((h) => h.flag.id === flag.id),
      `${flag.id} did not fire on: "${flag.provingUtterance}"`,
    );
  }
});

test('every end_call flag carries the sentence spoken in its place', () => {
  for (const flag of WARMUP_V1.redFlags.filter((f) => f.action === 'end_call')) {
    assert.ok(flag.say, `${flag.id} ends the call but says nothing`);
  }
});

test('an ordinary answer does not trip the gate', () => {
  for (const clean of [
    'my day is going well thanks',
    'a bit tired but otherwise fine',
    'I take ramipril 5 mg in the morning',
    '',
  ]) {
    assert.deepEqual(scan(clean, WARMUP_V1), { hits: [], blocked: false }, `tripped on: "${clean}"`);
  }
});

test('matching survives casing and punctuation', () => {
  const r = scan('Honestly? I WANT TO DIE, some days.', WARMUP_V1);
  assert.equal(r.blocked, true);
  assert.equal(r.action, 'end_call');
  assert.ok(r.say, 'a blocked turn must have something to say');
});

test('a blocked turn reports the sentence instead of leaving it to a model', () => {
  const r = scan('sometimes I think I should end my life', WARMUP_V1);
  assert.equal(r.blocked, true);
  assert.ok(r.say && r.say.length > 0);
});

test('word boundaries are respected at the edges of the transcript', () => {
  // "die" alone is not a pattern; "want to die" is. A substring of a longer
  // word must not match either.
  assert.equal(scan('the diet is going well', WARMUP_V1).hits.length, 0);
});
