import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WARMUP_V1 } from './config/protocol.ts';
import { FRAME_SIZE_MS, MODELS, TUNING } from './config/tuning.ts';
import { GroqProvider } from './inference/groq.ts';
import { scan } from './safety/scan.ts';
import { InterviewMachine } from './session/machine.ts';
import { SentenceChunker } from './voice/sentences.ts';
import { decodeWav } from './voice/wav.ts';

/**
 * Measures the real pipeline, against the real provider.
 *
 * The architecture note estimated ~700–900 ms to perceived first sound and
 * said plainly that it should be measured before being quoted to anyone.
 * This is that measurement. It runs the same code the call runs — same
 * provider, same models, same gate, same chunker — on a fixed utterance, so
 * the number is comparable run to run.
 *
 * What it cannot measure is the network between a patient and the SFU, and
 * the jitter buffer at each end. Add roughly 100 ms for that.
 */

const RUNS = Number(process.env.RUNS ?? 5);
const FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), 'voice/fixtures/speech-16k.wav');

const ai = new GroqProvider(process.env.GROQ_API_KEY ?? '');
const machine = new InterviewMachine(WARMUP_V1);
const { pcm } = decodeWav(readFileSync(FIXTURE));

/** The silence a patient sits through before we call their turn over. */
const endpointWait = TUNING.endpointSilenceFrames * FRAME_SIZE_MS;

interface Sample {
  stt: number;
  gate: number;
  llmFirstToken: number;
  ttsFirstAudio: number;
  toFirstSound: number;
  perceived: number;
  captured: number;
}

async function once(): Promise<Sample> {
  const t0 = Date.now();

  const transcript = await ai.transcribe(pcm);
  const tStt = Date.now();

  scan(transcript, WARMUP_V1);
  const tGate = Date.now();

  const history = [
    { role: 'system' as const, content: 'You are a clinical intake assistant on a phone call. One or two short sentences, spoken plainly.' },
    { role: 'assistant' as const, content: 'How is your day going?' },
    { role: 'user' as const, content: transcript },
  ];

  // Mirrors the real loop: a speech pass carrying no tools (so it always
  // speaks, and streams immediately) running concurrently with a silent
  // capture pass that writes the record. Only the speech pass is on the
  // patient's clock, so only it is counted toward first sound.
  let tFirstToken = 0;
  let firstSentence = '';

  const speechPass = async () => {
    const chunker = new SentenceChunker();
    for await (const event of ai.complete(history, {})) {
      if (event.type !== 'text') continue;
      tFirstToken ||= Date.now();
      const [chunk] = chunker.push(event.delta);
      if (chunk) {
        firstSentence = chunk;
        return; // TTS starts on sentence one; that is the whole point
      }
    }
    firstSentence ||= chunker.flush()[0] ?? '';
  };

  const capturePass = async () => {
    let calls = 0;
    for await (const event of ai.complete(history, { tools: machine.toolDefinitions() })) {
      if (event.type === 'tool') calls++;
    }
    return calls;
  };

  const [, captured] = await Promise.all([speechPass(), capturePass()]);
  if (!firstSentence) firstSentence = 'Thank you.';

  const tSentence = Date.now();
  await ai.speak(firstSentence.slice(0, 200));
  const tAudio = Date.now();

  return {
    stt: tStt - t0,
    gate: tGate - tStt,
    llmFirstToken: tFirstToken ? tFirstToken - tGate : -1,
    ttsFirstAudio: tAudio - tSentence,
    toFirstSound: tAudio - t0,
    perceived: endpointWait + (tAudio - t0),
    captured,
  };
}

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : Math.round((s[m - 1]! + s[m]!) / 2);
};

console.log(`models   stt=${MODELS.stt}  llm=${MODELS.llm}  tts=${MODELS.tts}`);
console.log(`runs     ${RUNS}\n`);

const samples: Sample[] = [];
for (let i = 0; i < RUNS; i++) {
  const s = await once();
  samples.push(s);
  console.log(
    `run ${i + 1}  stt ${String(s.stt).padStart(4)}ms · llm ${String(s.llmFirstToken).padStart(4)}ms · tts ${String(s.ttsFirstAudio).padStart(4)}ms → first sound ${String(s.toFirstSound).padStart(4)}ms  (captured ${s.captured})`,
  );
}

const rows: [string, number[]][] = [
  ['STT', samples.map((s) => s.stt)],
  ['safety gate', samples.map((s) => s.gate)],
  ['LLM first token', samples.map((s) => s.llmFirstToken)],
  ['TTS first audio', samples.map((s) => s.ttsFirstAudio)],
];

console.log('\nmedian, per stage');
for (const [label, xs] of rows) {
  console.log(`  ${label.padEnd(18)} ${String(median(xs)).padStart(5)} ms`);
}

const pipeline = median(samples.map((s) => s.toFirstSound));
console.log(`  ${'─'.repeat(24)}`);
console.log(`  ${'pipeline total'.padEnd(18)} ${String(pipeline).padStart(5)} ms`);
console.log(`  ${'endpoint wait'.padEnd(18)} ${String(endpointWait).padStart(5)} ms   (config, not a cost)`);
console.log(`  ${'network + jitter'.padEnd(18)} ${'~100'.padStart(5)} ms   (not measured here)`);
console.log(`  ${'═'.repeat(24)}`);
console.log(`  ${'PERCEIVED'.padEnd(18)} ${String(pipeline + endpointWait + 100).padStart(5)} ms   patient stops talking → hears a voice`);
const withCapture = samples.filter((s) => s.captured > 0).length;
console.log(`\n  architecture note estimated 700–900 ms for the pipeline`);
console.log(`  capture pass wrote a field on ${withCapture}/${RUNS} turns, off the critical path`);
