import { randomUUID } from 'node:crypto';
import { TTS_MAX_CHARS, TUNING } from '../config/tuning.ts';
import type { CompletionEvent, InferenceProvider, ToolCall } from '../inference/types.ts';
import { scan } from '../safety/scan.ts';
import { endSession, type Session } from '../session/store.ts';
import type { AssistantRoom } from '@metafora/media';
import { ReasoningFilter } from './reasoning.ts';
import { SentenceChunker } from './sentences.ts';
import { Endpointer } from './vad.ts';

/**
 * The conversation loop.
 *
 * Per turn: endpoint the patient's audio, transcribe it, run the safety gate
 * *before generation*, ask the model, and speak the answer sentence by
 * sentence. Every one of those steps is logged from here, because here is the
 * only place that sees all of them.
 *
 * The ordering in `runTurn` is the architecture. The transcript passes through
 * our code before it reaches the model and the model's reply passes through
 * our code before it reaches the patient, so the gate cannot be bypassed and
 * the tool matrix guards a call that physically cannot route around us.
 */
interface GenerationResult {
  spoken: string;
  toolCalls: ToolCall[];
  chunks: number;
  tFirstToken: number;
  tFirstSound: number;
}

/** Handed back to the model so it can see what its tool call actually did. */
type ToolOutcome = { ok: true; recorded: string } | { ok: false; error: string };

export class ConversationLoop {
  private endpointer: Endpointer | null = null;
  private speaking: AbortController | null = null;
  private turnRunning = false;
  /** When the endpointer handed us the turn, as opposed to when speech ended. */
  private turnStart = 0;
  private closed = false;

  constructor(
    private readonly session: Session,
    private readonly room: AssistantRoom,
    private readonly ai: InferenceProvider,
  ) {}

  async start(): Promise<void> {
    this.endpointer = await Endpointer.create();

    // The assistant speaks first: a patient who opened a link and tapped once
    // should not be met with silence and left guessing whose turn it is.
    await this.speakOpening();

    let track;
    try {
      track = await this.room.waitForPatientAudio();
    } catch (err) {
      // The patient is in the room but no microphone ever arrived. Saying so
      // is better than leaving them watching a bar that claims to be
      // listening to nothing.
      this.fail('patient_audio', err);
      await this.endCall(
        'error',
        'I am afraid I cannot hear you. Please check your microphone and start the conversation again.',
      );
      return;
    }
    this.room.send({ t: 'phase', phase: 'listening' });

    for await (const frame of this.room.patientFrames(track)) {
      if (this.closed) break;
      for (const event of await this.endpointer.push(frame)) {
        if (event.type === 'speech_sustained') this.onBargeIn();
        if (event.type === 'utterance') {
          // Fire and forget: the frame pump must keep draining so barge-in
          // stays responsive while a turn is being processed.
          void this.runTurn(event.pcm, event.durationMs, 'voice', event.speechEndedAt);
        }
      }
    }
  }

  /** A typed answer takes exactly the same path as a spoken one. */
  async submitTyped(text: string): Promise<void> {
    if (!text.trim()) return;
    await this.processTurn(text.trim(), 0, 'typed');
  }

  /**
   * The patient talked over the assistant.
   *
   * Interrupting is expected, so this is cheap and unconditional: drop the
   * queued audio, abort the in-flight generation, and let the turn that was
   * being spoken be discarded rather than recorded. An aborted reply that
   * stays in history corrupts the record, not just the experience.
   */
  private onBargeIn(): void {
    const inflight = this.speaking;
    if (!inflight || inflight.signal.aborted) return;
    this.room.interrupt();
    inflight.abort();
    this.room.send({ t: 'phase', phase: 'listening' });
  }

  private async runTurn(
    pcm: Int16Array,
    durationMs: number,
    source: 'voice',
    speechEndedAt: number,
  ) {
    if (this.turnRunning) return; // one turn at a time; the endpointer keeps buffering
    this.turnRunning = true;
    this.turnStart = Date.now();
    // The clock starts when the patient stopped talking, not when we noticed.
    const t0 = speechEndedAt;
    try {
      this.room.send({ t: 'phase', phase: 'thinking' });
      const transcript = await this.ai.transcribe(pcm, {
        prompt: this.session.protocol.clinician.name,
      });
      const tStt = Date.now();

      if (!transcript.trim()) {
        this.room.send({ t: 'phase', phase: 'listening' });
        return;
      }
      await this.processTurn(transcript, durationMs, source, { t0, tStt, tCommit: this.turnStart });
    } catch (err) {
      this.fail('turn', err);
    } finally {
      this.turnRunning = false;
    }
  }

  private async processTurn(
    transcript: string,
    durationMs: number,
    source: 'voice' | 'typed',
    timing?: { t0: number; tStt: number; tCommit: number },
  ) {
    const { session, room } = this;
    session.log.append({ type: 'turn.committed', transcript, durationMs, source });
    room.send({ t: 'utterance', who: 'patient', text: transcript, id: randomUUID() });

    // ── The gate. Before generation, always. ──
    const verdict = scan(transcript, session.protocol);
    session.log.append({
      type: 'safety.scanned',
      blocked: verdict.blocked,
      hits: verdict.hits.map((h) => h.flag.id),
      ...(verdict.action ? { action: verdict.action } : {}),
    });

    if (verdict.blocked) {
      await this.endCall('safety', verdict.say);
      return;
    }

    session.history.push({ role: 'user', content: transcript });

    const speaking = new AbortController();
    this.speaking = speaking;
    room.send({ t: 'phase', phase: 'speaking' });

    // ── two passes, run at the same time ──
    //
    // gpt-oss emits *either* speech or a tool call in a turn, never both — a
    // property of the harmony format, not of the prompt (a direct instruction
    // to do both was ignored in 5/5 trials). Running them in sequence put a
    // whole extra completion on the critical path and pushed perceived
    // latency to ~1.4 s.
    //
    // So they run concurrently. The speech pass carries no tools, which means
    // it always speaks and starts streaming immediately; the capture pass
    // writes the record silently while the audio is still playing. The
    // patient's clock only ever sees the speech pass.
    const [reply, calls] = await Promise.all([
      this.generate(speaking, { tools: false }),
      this.captureFields(speaking.signal),
    ]);
    if (speaking.signal.aborted) return this.discard(reply.spoken);

    session.history.push({
      role: 'assistant',
      content: reply.spoken,
      ...(calls.length ? { toolCalls: calls } : {}),
    });

    // Tool results go back into history even when nothing is asked of them: a
    // dangling tool call corrupts the next turn's context, and the model is
    // entitled to see what its call actually did.
    for (const call of calls) {
      const outcome = this.dispatchTool(call);
      session.history.push({
        role: 'tool',
        toolCallId: call.id,
        content: JSON.stringify(outcome),
      });
    }

    const spoken = reply.spoken;
    const chunks = reply.chunks;
    const tFirstSound = reply.tFirstSound;

    this.speaking = null;

    session.log.append({ type: 'llm.completed', text: spoken, toolCalls: calls.length });
    if (spoken) room.send({ t: 'utterance', who: 'assistant', text: spoken, id: randomUUID() });
    if (chunks) session.log.append({ type: 'tts.spoken', chars: spoken.length, chunks });

    if (timing) {
      session.log.append({
        type: 'latency.turn',
        ms: {
          // Silence the patient sat through before we called the turn over.
          // A design choice, but it is on their clock, so it is counted.
          endpoint_wait: timing.tCommit - timing.t0,
          stt: timing.tStt - timing.tCommit,
          llm_first_token: reply.tFirstToken ? reply.tFirstToken - timing.tStt : -1,
          tts_first_audio:
            tFirstSound && reply.tFirstToken ? tFirstSound - reply.tFirstToken : -1,
          // The number that actually matters: patient stops talking → hears a voice.
          perceived_first_sound: tFirstSound ? tFirstSound - timing.t0 : -1,
        },
      });
    }

    await this.advanceOrFinish();
  }


  /**
   * One pass of generation, spoken as it arrives.
   *
   * Reasoning is stripped from the stream *before* the chunker cuts it into
   * sentences: a `<think>` opened in one chunk and closed in another would
   * survive a per-chunk regex and be read aloud to a patient.
   */
  private async generate(
    speaking: AbortController,
    opts: { tools?: boolean } = {},
  ): Promise<GenerationResult> {
    const reasoning = new ReasoningFilter();
    const chunker = new SentenceChunker();
    const result: GenerationResult = {
      spoken: '',
      toolCalls: [],
      chunks: 0,
      tFirstToken: 0,
      tFirstSound: 0,
    };

    const speakChunks = async (chunks: string[]) => {
      for (const chunk of chunks) {
        if (speaking.signal.aborted) return;
        if (!(await this.say(chunk, speaking.signal))) return;
        result.tFirstSound ||= Date.now();
        result.spoken += `${result.spoken ? ' ' : ''}${chunk}`;
        result.chunks++;
      }
    };

    try {
      const stream = this.ai.complete(this.session.history, {
        signal: speaking.signal,
        ...(opts.tools === false ? {} : { tools: this.session.machine.toolDefinitions() }),
      });

      for await (const event of stream as AsyncIterable<CompletionEvent>) {
        if (speaking.signal.aborted) break;
        if (event.type === 'tool') {
          result.toolCalls.push(event.call);
          continue;
        }
        result.tFirstToken ||= Date.now();
        await speakChunks(chunker.push(reasoning.push(event.delta)));
      }
      await speakChunks([...chunker.push(reasoning.flush()), ...chunker.flush()]);
    } catch (err) {
      if (!speaking.signal.aborted) this.fail('generation', err);
    }
    return result;
  }

  /**
   * The capture pass: tool calls only, never spoken.
   *
   * Runs against the same history as the speech pass and extracts from the
   * patient's answer, so it does not need to wait for the reply to be written.
   * Any prose it returns is discarded — this pass exists to write the record,
   * and the patient is already being spoken to by the other one.
   */
  private async captureFields(signal: AbortSignal): Promise<ToolCall[]> {
    const calls: ToolCall[] = [];
    try {
      for await (const event of this.ai.complete(this.session.history, {
        signal,
        tools: this.session.machine.toolDefinitions(),
      })) {
        if (event.type === 'tool') calls.push(event.call);
      }
    } catch (err) {
      if (!signal.aborted) this.fail('capture', err);
    }
    return calls;
  }

  /**
   * The reply was abandoned mid-sentence. It is not what the assistant said,
   * so it goes in neither the history nor the record — an aborted turn left in
   * history corrupts the record, not just the experience.
   */
  private discard(spoken: string): void {
    this.session.log.append({ type: 'turn.aborted', reason: 'barge_in', discardedText: spoken });
    this.speaking = null;
  }

  /** Speak one chunk. Returns false if the patient cut in mid-sentence. */
  private async say(text: string, signal: AbortSignal): Promise<boolean> {
    const clean = sanitise(text);
    if (!clean) return true;
    if (signal.aborted) return false;

    const pcm = await this.ai.speak(clean.slice(0, TTS_MAX_CHARS), { signal });
    if (signal.aborted) return false;
    await this.room.play(pcm, signal);
    if (signal.aborted) return false;

    // A beat between sentences. The pause after a question is longer, because
    // that silence is doing a job: it tells the patient it is their turn.
    const pause = clean.endsWith('?') ? TUNING.questionPauseMs : TUNING.sentencePauseMs;
    if (pause > 0) await this.room.playSilence(pause, signal);
    return !signal.aborted;
  }

  /**
   * Tool dispatch, after authorisation, in our process.
   *
   * The permission matrix is checked here rather than described to the model,
   * so a call from a state the protocol does not allow is refused whatever the
   * model decided.
   */
  private dispatchTool(call: ToolCall): ToolOutcome {
    const { session, room } = this;
    const auth = session.machine.authorise(call.name);

    let args: { field?: string; value?: string } = {};
    try {
      args = JSON.parse(call.arguments);
    } catch {
      session.log.append({
        type: 'tool.called',
        name: call.name,
        args: call.arguments,
        authorised: false,
        reason: 'arguments were not valid JSON',
      });
      return { ok: false, error: 'arguments were not valid JSON' };
    }

    if (!auth.authorised) {
      session.log.append({
        type: 'tool.called',
        name: call.name,
        args,
        authorised: false,
        ...(auth.reason ? { reason: auth.reason } : {}),
      });
      return { ok: false, error: auth.reason ?? 'refused' };
    }

    const captured =
      typeof args.field === 'string' &&
      typeof args.value === 'string' &&
      session.machine.capture(args.field, args.value);

    session.log.append({
      type: 'tool.called',
      name: call.name,
      args,
      authorised: Boolean(captured),
      ...(captured ? {} : { reason: 'field is not in this protocol' }),
    });

    if (captured) {
      room.send({ t: 'notes', fields: session.machine.fields() });
      return { ok: true, recorded: args.field! };
    }
    return { ok: false, error: 'field is not in this protocol' };
  }

  private async advanceOrFinish(): Promise<void> {
    const { session, room } = this;
    const state = session.machine.current;
    if (!state) return;

    // A question is done once its field is captured. Nothing else advances the
    // interview — no counter, no turn budget.
    if (session.machine.captured[state.question.fieldKey]) {
      const next = session.machine.advance();
      session.log.append({
        type: 'state.transition',
        from: state.id,
        to: next?.id ?? 'complete',
      });
      room.send({ t: 'notes', fields: session.machine.fields() });

      if (session.machine.complete) {
        await this.room.waitForPlayout();
        await this.endCall('complete');
        return;
      }
    }
    room.send({ t: 'phase', phase: 'listening' });
  }

  private async speakOpening(): Promise<void> {
    const { session, room } = this;
    const state = session.machine.current;
    if (!state) return;

    const opening = [
      `Hello ${session.interview.patient.firstName}.`,
      `${session.protocol.clinician.name} asked me to have a quick chat with you before your appointment.`,
      'Just talk normally, and jump in whenever you like.',
      state.question.ask,
    ].join(' ');

    const speaking = new AbortController();
    this.speaking = speaking;
    room.send({ t: 'phase', phase: 'speaking' });
    room.send({ t: 'utterance', who: 'assistant', text: opening, id: randomUUID() });

    const t0 = Date.now();
    const chunker = new SentenceChunker();
    let chunks = 0;
    for (const chunk of [...chunker.push(opening), ...chunker.flush()]) {
      if (speaking.signal.aborted) break;
      await this.say(chunk, speaking.signal);
      chunks++;
    }

    session.history.push({ role: 'assistant', content: opening });
    // The opening is spoken to the patient, so it belongs in the record as
    // much as anything the model generates later.
    session.log.append({ type: 'opening.spoken', text: opening, chunks, ms: Date.now() - t0 });
    this.speaking = null;
  }

  private async endCall(reason: 'complete' | 'safety' | 'error', say?: string): Promise<void> {
    if (this.closed) return;
    this.closed = true;

    if (say) {
      const chunker = new SentenceChunker();
      const closing = new AbortController();
      this.speaking = closing;
      this.room.send({ t: 'phase', phase: 'speaking' });
      this.room.send({ t: 'utterance', who: 'assistant', text: say, id: randomUUID() });
      for (const chunk of [...chunker.push(say), ...chunker.flush()]) {
        // A safety closure is the one thing a patient may not talk over.
        await this.say(chunk, closing.signal);
      }
      await this.room.waitForPlayout();
    }

    endSession(this.session, reason);
    // The notes card stays exactly where it is. Nothing is submitted at the
    // end, because everything was saved as it was said.
    this.room.send({ t: 'notes', fields: this.session.machine.fields() });
    this.room.send({ t: 'phase', phase: 'ended' });
    this.room.send({ t: 'ended', reason, ...(say ? { say } : {}) });
  }

  private fail(where: string, err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    this.session.log.append({ type: 'error', where, message });
    console.error(`[loop] ${where}:`, err);
  }

  async stop(): Promise<void> {
    this.closed = true;
    this.speaking?.abort();
    endSession(this.session, this.session.endedReason ?? 'disconnected');
  }
}

/**
 * Strip anything that should never be read aloud.
 *
 * gpt-oss is a reasoning model. `reasoning_format: 'hidden'` keeps its chain
 * of thought out of the reply, and this is the belt to that braces: reasoning
 * spoken to a patient would be a clinical failure, not a cosmetic one. Markdown
 * is stripped for the same reason — nobody wants to hear an asterisk.
 */
function sanitise(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<\/?think>/gi, '')
    .replace(/[*_`#]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export const TURN_TUNING = TUNING;
