import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import type { ServerMessage } from '@metafora/contracts';
import { WARMUP_V1 } from '../config/protocol.ts';
import { TUNING } from '../config/tuning.ts';
import type {
  ChatMessage,
  CompleteOptions,
  CompletionEvent,
  InferenceProvider,
} from '../inference/types.ts';
import type { LogEvent } from '../session/log.ts';
import { createSession, type Session } from '../session/store.ts';
import { ConversationLoop } from './loop.ts';
import type { AssistantRoom } from '@metafora/media';

/**
 * The turn pipeline, end to end, without touching a network.
 *
 * `submitTyped` takes exactly the same path as a spoken answer — gate, state
 * machine, generation, tool dispatch — so driving the loop through it exercises
 * the ordering guarantees that the whole architecture exists to provide.
 */

const INTERVIEW = { id: 'iv_test', protocolId: WARMUP_V1.id, patient: { id: 'pt_t', firstName: 'Alice' } };

class StubProvider implements InferenceProvider {
  spoken: string[] = [];
  calls = 0;
  toolsSeen: boolean[] = [];
  /**
   * Script 0 answers the speech pass, script 1 the concurrent capture pass.
   * They are issued in that order by Promise.all.
   */
  private readonly scripts: CompletionEvent[][];

  constructor(...scripts: CompletionEvent[][]) {
    this.scripts = scripts;
  }

  async transcribe(): Promise<string> {
    throw new Error('not used: typed turns skip STT');
  }

  async *complete(_m: ChatMessage[], opts?: CompleteOptions): AsyncIterable<CompletionEvent> {
    this.toolsSeen.push(Boolean(opts?.tools?.length));
    const script = this.scripts[this.calls++] ?? [];
    for (const event of script) yield event;
  }

  async speak(text: string): Promise<Int16Array> {
    this.spoken.push(text);
    return new Int16Array(480);
  }
}

function harness(...scripts: CompletionEvent[][]) {
  const sent: ServerMessage[] = [];
  const logged: LogEvent[] = [];

  const session = createSession(INTERVIEW, WARMUP_V1) as Session;
  const original = session.log.append.bind(session.log);
  session.log.append = (e: LogEvent) => {
    logged.push(e);
    original(e);
  };

  const pauses: number[] = [];
  const room = {
    send: (m: ServerMessage) => sent.push(m),
    play: async () => {},
    playSilence: async (ms: number) => {
      pauses.push(ms);
    },
    interrupt: () => {},
    waitForPlayout: async () => {},
  } as unknown as AssistantRoom;

  const ai = new StubProvider(...scripts);
  return { loop: new ConversationLoop(session, room, ai), session, sent, logged, ai, pauses };
}

const say = (text: string): CompletionEvent[] =>
  text.split(' ').map((w, i) => ({ type: 'text', delta: i ? ` ${w}` : w }));

test('the safety gate runs before generation, every turn', async () => {
  const h = harness(say('Glad to hear it.'));
  await h.loop.submitTyped('my day is going well');

  const order = h.logged.map((e) => e.type);
  const gate = order.indexOf('safety.scanned');
  const generated = order.indexOf('llm.completed');

  assert.ok(gate !== -1, 'the gate must run');
  assert.ok(generated !== -1, 'generation must have happened');
  assert.ok(gate < generated, 'the gate must precede generation, not observe it');
});

test('a red-flag turn ends the call with zero model invocations', async () => {
  const h = harness(say('this must never be generated'));
  await h.loop.submitTyped('honestly I want to die some days');

  assert.equal(
    h.logged.some((e) => e.type === 'llm.completed'),
    false,
    'the model must not be in the loop when the gate blocks',
  );

  const scanned = h.logged.find((e) => e.type === 'safety.scanned') as any;
  assert.equal(scanned.blocked, true);
  assert.ok(h.sent.some((m) => m.t === 'ended' && m.reason === 'safety'));
  assert.ok(h.ai.spoken.length > 0, 'the patient must still be told what is happening');
});

test('a tool call fills the notes card and captures the field', async () => {
  const h = harness(say('Thanks for telling me.'), [
    {
      type: 'tool',
      call: {
        id: 't1',
        name: 'update_intake',
        arguments: JSON.stringify({ field: 'day_mood', value: 'a bit tired but fine' }),
      },
    },
  ]);
  await h.loop.submitTyped('a bit tired but fine');

  assert.deepEqual(h.session.machine.captured, { day_mood: 'a bit tired but fine' });

  const notes = h.sent.filter((m) => m.t === 'notes').at(-1) as any;
  assert.equal(notes.fields[0].value, 'a bit tired but fine');
  assert.equal(notes.fields[0].status, 'captured');
});

test('a tool call for an undeclared field is refused', async () => {
  const h = harness(say('Noted.'), [
    {
      type: 'tool',
      call: {
        id: 't1',
        name: 'update_intake',
        arguments: JSON.stringify({ field: 'nhs_number', value: '123 456 7890' }),
      },
    },
  ]);
  await h.loop.submitTyped('my number is 123 456 7890');

  const call = h.logged.find((e) => e.type === 'tool.called') as any;
  assert.equal(call.authorised, false);
  assert.deepEqual(h.session.machine.captured, { day_mood: null });
});

test('malformed tool arguments are refused, not thrown', async () => {
  const h = harness(say('Right.'), [
    { type: 'tool', call: { id: 't1', name: 'update_intake', arguments: '{not json' } },
  ]);
  await h.loop.submitTyped('fine thanks');

  const call = h.logged.find((e) => e.type === 'tool.called') as any;
  assert.equal(call.authorised, false);
  assert.match(call.reason, /valid JSON/);
});

test('reasoning that leaks into content is never spoken', async () => {
  const h = harness(say('<think>The patient sounds tired, I should probe.</think> Glad to hear it.'));
  await h.loop.submitTyped('doing well thanks');

  const everything = h.ai.spoken.join(' ');
  assert.ok(!/<think>|probe/i.test(everything), `reasoning reached TTS: "${everything}"`);
  assert.match(everything, /Glad to hear it/);
});

test('the interview ends once its last field is captured', async () => {
  const h = harness(say('Lovely, thank you.'), [
    {
      type: 'tool',
      call: {
        id: 't1',
        name: 'update_intake',
        arguments: JSON.stringify({ field: 'day_mood', value: 'good' }),
      },
    },
  ]);
  await h.loop.submitTyped('good thanks');

  assert.equal(h.session.machine.complete, true);
  assert.ok(h.sent.some((m) => m.t === 'ended' && m.reason === 'complete'));
  // The notes card stays on screen, complete. Nothing is submitted at the end.
  assert.equal((h.sent.filter((m) => m.t === 'notes').at(-1) as any).fields[0].value, 'good');
});

test('the patient bubble is emitted before the assistant replies', async () => {
  const h = harness(say('Good to hear.'));
  await h.loop.submitTyped('all fine here');

  const utterances = h.sent.filter((m) => m.t === 'utterance') as any[];
  assert.equal(utterances[0].who, 'patient');
  assert.equal(utterances[0].text, 'all fine here');
  assert.equal(utterances[1].who, 'assistant');
});

test('speech and capture run as two concurrent passes', async () => {
  // The speech pass carries no tools so it always speaks; the capture pass
  // writes the record while that audio is still playing. Sequencing them
  // instead put a whole extra completion on the patient's clock.
  const h = harness(say('Glad to hear it, thank you.'), [
    {
      type: 'tool',
      call: {
        id: 't1',
        name: 'update_intake',
        arguments: JSON.stringify({ field: 'day_mood', value: 'pretty well' }),
      },
    },
  ]);
  await h.loop.submitTyped('pretty well thanks');

  assert.equal(h.ai.calls, 2, 'one pass to speak, one to capture');
  assert.match(h.ai.spoken.join(' '), /Glad to hear it/);
  assert.equal(h.session.machine.captured.day_mood, 'pretty well');
  assert.ok(h.sent.some((m) => m.t === 'utterance' && m.who === 'assistant'));
});

test('the speech pass is never given tools', async () => {
  const h = harness(say('Right, thank you.'), []);
  await h.loop.submitTyped('fine');
  assert.equal(h.ai.toolsSeen[0], false, 'tools on the speech pass cost a silent turn');
  assert.equal(h.ai.toolsSeen[1], true, 'the capture pass is the one that needs them');
});

test('a tool result is fed back into history', async () => {
  const h = harness(say('Noted.'), [
    {
      type: 'tool',
      call: {
        id: 't1',
        name: 'update_intake',
        arguments: JSON.stringify({ field: 'day_mood', value: 'fine' }),
      },
    },
  ]);
  await h.loop.submitTyped('fine thanks');

  const toolMsg = h.session.history.find((m) => m.role === 'tool');
  assert.ok(toolMsg, 'a tool call with no result corrupts the next turn');
  assert.equal(toolMsg!.toolCallId, 't1');
  assert.match(toolMsg!.content, /"ok":true/);

  // The assistant message carrying the call must precede its result.
  const assistantAt = h.session.history.findIndex((m) => m.toolCalls?.length);
  const toolAt = h.session.history.findIndex((m) => m.role === 'tool');
  assert.ok(assistantAt !== -1 && assistantAt < toolAt);
});

test('a refused tool reports the refusal back to the model', async () => {
  const h = harness(say('Right.'), [
    {
      type: 'tool',
      call: {
        id: 't1',
        name: 'update_intake',
        arguments: JSON.stringify({ field: 'nhs_number', value: '123' }),
      },
    },
  ]);
  await h.loop.submitTyped('my number is 123');

  const toolMsg = h.session.history.find((m) => m.role === 'tool');
  assert.match(toolMsg!.content, /"ok":false/);
});

test('a beat of silence follows each spoken sentence', async () => {
  const h = harness(say('Glad to hear it. Thanks for telling me.'));
  await h.loop.submitTyped('all fine');

  assert.equal(h.pauses.length, h.ai.spoken.length, 'every sentence gets its beat');
  assert.ok(h.pauses.every((ms) => ms === TUNING.sentencePauseMs));
});

test('a question gets a longer beat than a statement', async () => {
  const h = harness(say('Right. And how are you sleeping?'));
  await h.loop.submitTyped('fine');

  // The silence after a question is doing a job: it says it is your turn.
  assert.ok(h.pauses.includes(TUNING.questionPauseMs));
  assert.ok(TUNING.questionPauseMs > TUNING.sentencePauseMs);
});
