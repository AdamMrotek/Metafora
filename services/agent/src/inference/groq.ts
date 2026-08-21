import { MODELS, OUTPUT_SAMPLE_RATE, SAMPLE_RATE, TTS_MAX_CHARS } from '../config/tuning.ts';
import { decodeWav, encodeWav } from '../voice/wav.ts';
import type {
  ChatMessage,
  CompleteOptions,
  CompletionEvent,
  InferenceProvider,
  ToolCall,
  TranscribeOptions,
} from './types.ts';

const BASE = 'https://api.groq.com/openai/v1';

/**
 * The Groq implementation of the three interfaces. The only file in the server
 * that mentions a vendor or a model id.
 *
 * Two provider quirks are load-bearing and handled here rather than leaking:
 *
 *  · gpt-oss is a *reasoning* model. Without `reasoning_format: 'hidden'` its
 *    chain of thought arrives in `message.content` and would be read aloud to
 *    a patient. `reasoning_effort: 'low'` keeps a voice turn responsive.
 *
 *  · Orpheus rejects input over 200 characters, so `speak()` refuses rather
 *    than silently truncating — the caller chunks at sentence boundaries and
 *    a truncated clinical sentence is worse than a loud failure.
 */
export class GroqProvider implements InferenceProvider {
  constructor(private readonly apiKey: string) {}

  /**
   * Checked at first use rather than construction, so the server boots and the
   * portal renders without a key. The call then fails at the first turn with a
   * message that says what is wrong, which is where it is actionable.
   */
  private headers(extra: Record<string, string> = {}) {
    if (!this.apiKey) throw new Error('GROQ_API_KEY is not set');
    return { Authorization: `Bearer ${this.apiKey}`, ...extra };
  }

  async transcribe(pcm: Int16Array, opts: TranscribeOptions = {}): Promise<string> {
    const form = new FormData();
    const wav = encodeWav(pcm, SAMPLE_RATE);
    form.append('file', new Blob([wav], { type: 'audio/wav' }), 'utterance.wav');
    form.append('model', MODELS.stt);
    form.append('response_format', 'text');
    form.append('temperature', '0');
    if (opts.prompt) form.append('prompt', opts.prompt);

    const res = await fetch(`${BASE}/audio/transcriptions`, {
      method: 'POST',
      headers: this.headers(),
      body: form,
      signal: opts.signal,
    });
    if (!res.ok) throw new Error(`STT ${res.status}: ${await res.text()}`);
    return (await res.text()).trim();
  }

  async *complete(
    messages: ChatMessage[],
    opts: CompleteOptions = {},
  ): AsyncIterable<CompletionEvent> {
    const res = await fetch(`${BASE}/chat/completions`, {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      signal: opts.signal,
      body: JSON.stringify({
        model: MODELS.llm,
        stream: true,
        temperature: 0.3,
        max_completion_tokens: 400,
        // Both are required for voice: hidden keeps chain-of-thought out of
        // content, low keeps the first token inside the latency budget.
        reasoning_format: 'hidden',
        reasoning_effort: 'low',
        messages: messages.map(toWireMessage),
        ...(opts.tools?.length
          ? {
              tool_choice: 'auto',
              tools: opts.tools.map((t) => ({
                type: 'function',
                function: {
                  name: t.name,
                  description: t.description,
                  parameters: t.parameters,
                },
              })),
            }
          : {}),
      }),
    });
    if (!res.ok) throw new Error(`LLM ${res.status}: ${await res.text()}`);
    if (!res.body) throw new Error('LLM response had no body');

    // Tool calls arrive in fragments across deltas, indexed by position.
    const partial = new Map<number, { id: string; name: string; args: string }>();

    for await (const data of sseEvents(res.body, opts.signal)) {
      const choice = data?.choices?.[0];
      if (!choice) continue;

      const text = choice.delta?.content;
      if (typeof text === 'string' && text.length > 0) {
        yield { type: 'text', delta: text };
      }

      for (const tc of choice.delta?.tool_calls ?? []) {
        const slot = partial.get(tc.index) ?? { id: '', name: '', args: '' };
        if (tc.id) slot.id = tc.id;
        if (tc.function?.name) slot.name = tc.function.name;
        if (tc.function?.arguments) slot.args += tc.function.arguments;
        partial.set(tc.index, slot);
      }

      if (choice.finish_reason) {
        for (const slot of partial.values()) {
          if (slot.name) {
            yield {
              type: 'tool',
              call: { id: slot.id || slot.name, name: slot.name, arguments: slot.args || '{}' },
            } satisfies { type: 'tool'; call: ToolCall };
          }
        }
        partial.clear();
      }
    }
  }

  async speak(text: string, opts: { signal?: AbortSignal } = {}): Promise<Int16Array> {
    if (text.length > TTS_MAX_CHARS) {
      throw new Error(`TTS input is ${text.length} chars; Orpheus caps at ${TTS_MAX_CHARS}`);
    }
    const res = await fetch(`${BASE}/audio/speech`, {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      signal: opts.signal,
      body: JSON.stringify({
        model: MODELS.tts,
        voice: MODELS.ttsVoice,
        input: text,
        response_format: 'wav',
        // Asking for the room's rate means nothing is resampled on the way out.
        sample_rate: OUTPUT_SAMPLE_RATE,
      }),
    });
    if (!res.ok) throw new Error(`TTS ${res.status}: ${await res.text()}`);

    const { pcm, sampleRate } = decodeWav(Buffer.from(await res.arrayBuffer()));
    if (sampleRate !== OUTPUT_SAMPLE_RATE) {
      throw new Error(`TTS returned ${sampleRate} Hz, expected ${OUTPUT_SAMPLE_RATE}`);
    }
    return pcm;
  }
}

function toWireMessage(m: ChatMessage) {
  if (m.role === 'tool') {
    return { role: 'tool', content: m.content, tool_call_id: m.toolCallId };
  }
  if (m.toolCalls?.length) {
    return {
      role: m.role,
      content: m.content,
      tool_calls: m.toolCalls.map((c) => ({
        id: c.id,
        type: 'function',
        function: { name: c.name, arguments: c.arguments },
      })),
    };
  }
  return { role: m.role, content: m.content };
}

/** Minimal SSE reader: yields parsed `data:` payloads until `[DONE]`. */
async function* sseEvents(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncIterable<any> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) return;

      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') return;
        try {
          yield JSON.parse(payload);
        } catch {
          // A partial frame split across chunks; the next read completes it.
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}
