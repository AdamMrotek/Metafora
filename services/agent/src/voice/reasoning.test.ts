import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { ReasoningFilter } from './reasoning.ts';

/** Feed a reply one character at a time, which is the worst case for tags. */
function perChar(text: string): string {
  const f = new ReasoningFilter();
  let out = '';
  for (const ch of text) out += f.push(ch);
  return out + f.flush();
}

/** Feed it in arbitrary lumps, which is what a real stream does. */
function inChunks(chunks: string[]): string {
  const f = new ReasoningFilter();
  let out = '';
  for (const c of chunks) out += f.push(c);
  return out + f.flush();
}

test('passes ordinary text through untouched', () => {
  assert.equal(perChar('Glad to hear it. How are you sleeping?'), 'Glad to hear it. How are you sleeping?');
});

test('removes a reasoning block', () => {
  assert.equal(perChar('<think>they sound tired</think>Glad to hear it.'), 'Glad to hear it.');
});

test('removes a block whose tags are split across deltas', () => {
  // This is the case that made it into production behaviour once already:
  // the opening and closing tags landing in different chunks.
  const out = inChunks(['<th', 'ink>the patient sounds tired, I should pro', 'be.</thi', 'nk> Glad to hear it.']);
  assert.equal(out.trim(), 'Glad to hear it.');
  assert.ok(!/probe|tired/i.test(out));
});

test('keeps text on both sides of a block', () => {
  assert.equal(perChar('Right. <think>hmm</think> And how are you sleeping?'), 'Right.  And how are you sleeping?');
});

test('drops an unterminated reasoning block entirely', () => {
  // The model stopped mid-thought. Saying the thought out loud is the failure.
  assert.equal(perChar('Fine. <think>I wonder whether they are'), 'Fine. ');
});

test('does not hold back text that merely looks like a tag opening', () => {
  const out = perChar('The reading was < 5 and the result was fine.');
  assert.equal(out, 'The reading was < 5 and the result was fine.');
});

test('handles several blocks in one reply', () => {
  assert.equal(perChar('<think>a</think>One.<think>b</think>Two.'), 'One.Two.');
});

test('emits nothing at all for a reply that is only reasoning', () => {
  assert.equal(perChar('<think>all of it</think>'), '');
});
