/**
 * The three interfaces every inference provider sits behind.
 *
 * Groq is US-hosted. For identifiable patient data that raises UK GDPR
 * international-transfer obligations that "our SFU is in-region" does not
 * answer. Defining these now costs a few hours and is the difference between
 * a procurement conversation and a re-platforming: swapping to faster-whisper,
 * vLLM and Piper behind the same three signatures is a deployment change.
 *
 * Nothing outside `inference/` may import a provider SDK or mention a model id.
 */

export interface TranscribeOptions {
  signal?: AbortSignal;
  /** Steers spelling of names and terms the model would otherwise guess at. */
  prompt?: string;
}

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  toolCallId?: string;
  toolCalls?: ToolCall[];
}

export interface ToolCall {
  id: string;
  name: string;
  /** Raw JSON string as emitted by the model; parsed and validated by us. */
  arguments: string;
}

export interface ToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

/**
 * The architecture note specified `complete(messages) => AsyncIterable<string>`.
 * That signature cannot carry a tool call, and the tool call is how the record
 * gets written — so the stream yields a small event union instead. The seam is
 * unchanged; only the element type is wider.
 */
export type CompletionEvent =
  | { type: 'text'; delta: string }
  | { type: 'tool'; call: ToolCall };

export interface CompleteOptions {
  signal?: AbortSignal;
  tools?: ToolDefinition[];
}

export interface InferenceProvider {
  /** Utterance PCM (16 kHz mono) to text. */
  transcribe(pcm: Int16Array, opts?: TranscribeOptions): Promise<string>;

  /** History plus system prompt to a reply, streamed. */
  complete(messages: ChatMessage[], opts?: CompleteOptions): AsyncIterable<CompletionEvent>;

  /** Reply text to PCM at OUTPUT_SAMPLE_RATE, mono. */
  speak(text: string, opts?: { signal?: AbortSignal }): Promise<Int16Array>;
}
