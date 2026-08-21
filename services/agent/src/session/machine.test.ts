import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { WARMUP_V1 } from '../config/protocol.ts';
import { InterviewMachine } from './machine.ts';

test('compiles the script into fully-qualified states', () => {
  const m = new InterviewMachine(WARMUP_V1);
  assert.deepEqual(m.states.map((s) => s.id), ['s1.q1']);
});

test('authorises a tool only from a state the matrix names', () => {
  const m = new InterviewMachine(WARMUP_V1);
  assert.equal(m.authorise('update_intake').authorised, true);

  const unknown = m.authorise('exfiltrate_record');
  assert.equal(unknown.authorised, false);
  assert.match(unknown.reason!, /not in protocol/);
});

test('refuses tools once the interview is complete', () => {
  const m = new InterviewMachine(WARMUP_V1);
  m.advance();
  assert.equal(m.complete, true);
  assert.equal(m.authorise('update_intake').authorised, false);
});

test('refuses to capture a field the protocol never declared', () => {
  const m = new InterviewMachine(WARMUP_V1);
  assert.equal(m.capture('day_mood', 'pretty good'), true);
  assert.equal(m.capture('nhs_number', '123 456 7890'), false);
  assert.deepEqual(m.captured, { day_mood: 'pretty good' });
});

test('field states drive the notes card', () => {
  const m = new InterviewMachine(WARMUP_V1);
  assert.deepEqual(m.fields(), [
    { key: 'day_mood', label: 'How the day is going', value: null, status: 'live' },
  ]);

  m.capture('day_mood', 'a bit tired');
  assert.equal(m.fields()[0]!.status, 'captured');
  assert.equal(m.fields()[0]!.value, 'a bit tired');
});

test('an unanswered question left behind is marked open, not captured', () => {
  const m = new InterviewMachine(WARMUP_V1);
  m.advance();
  assert.equal(m.fields()[0]!.status, 'open');
});

test('tool schema only ever offers declared field keys', () => {
  const m = new InterviewMachine(WARMUP_V1);
  const [def] = m.toolDefinitions();
  const props = (def!.parameters as any).properties;
  assert.deepEqual(props.field.enum, ['day_mood']);
});

test('follow-up budget is bounded per question', () => {
  const m = new InterviewMachine(WARMUP_V1);
  assert.equal(m.recordFollowUp('s1.q1'), 1);
  assert.equal(m.recordFollowUp('s1.q1'), 2);
});
