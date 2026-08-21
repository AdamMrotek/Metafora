import { TTS_MAX_CHARS } from '../config/tuning.ts';

/**
 * Cuts a streaming reply into speakable chunks.
 *
 * This is where most of the difference between "responsive" and "phone tree"
 * lives: TTS starts on sentence one rather than on the finished reply, so the
 * patient hears the first words while the model is still writing the rest.
 *
 * Orpheus caps input at 200 characters, so the chunker has two jobs — find
 * sentence boundaries, and guarantee no chunk exceeds the cap. It never cuts
 * mid-word: a clinical sentence sliced through a drug name is worse than a
 * slightly early break.
 */

/** Terminators that end a spoken sentence. */
const TERMINATORS = new Set(['.', '!', '?', '\n']);

/**
 * Abbreviations whose trailing full stop does not end a sentence. Without
 * this, "Dr. Hollis" becomes two TTS calls with an audible seam in the middle
 * of the clinician's name.
 */
const ABBREVIATIONS = ['dr', 'mr', 'mrs', 'ms', 'prof', 'st', 'e.g', 'i.e', 'approx', 'no'];

export class SentenceChunker {
  private buffer = '';
  /** Complete sentences held back to be packed into one request. */
  private pack = '';
  private emitted = 0;

  /**
   * @param packAfterFirst Emit the first sentence alone, then pack later ones
   *   up to the character cap.
   *
   *   Every chunk is one TTS request, and the provider's limits are on
   *   *requests*, not characters — 10 per minute on the free tier. Speaking a
   *   four-sentence reply as four requests burns the budget for no benefit,
   *   because only the first chunk is on the latency path. So the first
   *   sentence goes out the moment it is ready, and everything after it rides
   *   along in as few requests as the cap allows.
   */
  constructor(private readonly packAfterFirst = true) {}

  /** Feed a delta; get back whatever complete chunks it produced. */
  push(delta: string): string[] {
    this.buffer += delta;
    return this.collect(this.drain(false), false);
  }

  /** End of stream: emit whatever is left, terminated or not. */
  flush(): string[] {
    const out = this.drain(true);
    const rest = this.buffer.trim();
    this.buffer = '';
    if (rest) out.push(...hardWrap(rest));
    return this.collect(out, true);
  }

  /**
   * Hold complete sentences back and release them in cap-sized groups. The
   * first one is never held: it is the only chunk the patient is waiting on.
   */
  private collect(sentences: string[], final: boolean): string[] {
    if (!this.packAfterFirst) {
      this.emitted += sentences.length;
      return sentences;
    }
    const out: string[] = [];

    for (const sentence of sentences) {
      if (this.emitted === 0 && !this.pack) {
        out.push(sentence);
        this.emitted++;
        continue;
      }
      const joined = this.pack ? `${this.pack} ${sentence}` : sentence;
      if (joined.length > TTS_MAX_CHARS) {
        if (this.pack) {
          out.push(this.pack);
          this.emitted++;
        }
        this.pack = sentence;
      } else {
        this.pack = joined;
      }
    }

    if (final && this.pack) {
      out.push(this.pack);
      this.emitted++;
      this.pack = '';
    }
    return out;
  }

  private drain(final: boolean): string[] {
    const out: string[] = [];

    for (;;) {
      const cut = this.findBoundary();
      if (cut === -1) break;
      const chunk = this.buffer.slice(0, cut).trim();
      this.buffer = this.buffer.slice(cut);
      if (chunk) out.push(...hardWrap(chunk));
    }

    // Nothing terminated yet, but we are already past what Orpheus accepts:
    // break at the last safe word boundary rather than wait for a full stop.
    while (!final && this.buffer.length > TTS_MAX_CHARS) {
      const cut = lastBreakBefore(this.buffer, TTS_MAX_CHARS);
      const chunk = this.buffer.slice(0, cut).trim();
      this.buffer = this.buffer.slice(cut);
      if (chunk) out.push(chunk);
      else break;
    }

    return out;
  }

  /** Index just past the next real sentence terminator, or -1. */
  private findBoundary(): number {
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i]!;
      if (!TERMINATORS.has(ch)) continue;

      if (ch === '.') {
        // A decimal point ("5.5 mg") is not a boundary.
        const prev = this.buffer[i - 1];
        const next = this.buffer[i + 1];
        if (prev && next && /\d/.test(prev) && /\d/.test(next)) continue;
        if (this.endsWithAbbreviation(i)) continue;
        // Mid-stream, a trailing "." with nothing after it may still become
        // "5.5" once the next delta lands. Wait for one more character.
        if (next === undefined) return -1;
      }

      // Absorb closing quotes and brackets so they stay with the sentence.
      let end = i + 1;
      while (end < this.buffer.length && /["'”’)\]]/.test(this.buffer[end]!)) end++;
      return end;
    }
    return -1;
  }

  private endsWithAbbreviation(dotIndex: number): boolean {
    const before = this.buffer.slice(0, dotIndex);
    const word = before.split(/[\s(]/).pop() ?? '';

    // A lone capital is an initial, not a sentence end. Without this,
    // "Dr E. Hollis" is spoken as two requests with an audible seam through
    // the middle of the clinician's name.
    if (/^[A-Z]$/.test(word)) return true;

    return ABBREVIATIONS.includes(word.toLowerCase());
  }
}

/** Split an over-long chunk at word boundaries so nothing exceeds the cap. */
function hardWrap(text: string): string[] {
  if (text.length <= TTS_MAX_CHARS) return [text];

  const out: string[] = [];
  let rest = text;
  while (rest.length > TTS_MAX_CHARS) {
    const cut = lastBreakBefore(rest, TTS_MAX_CHARS);
    out.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) out.push(rest);
  return out.filter(Boolean);
}

/**
 * Last point at or before `limit` that is safe to cut: a comma or clause break
 * if there is one, otherwise a space. Falls back to a hard cut only if a
 * single "word" is longer than the whole budget.
 */
function lastBreakBefore(text: string, limit: number): number {
  const window = text.slice(0, limit);
  for (const re of [/[,;:—–]\s(?=[^,;:—–]*$)/, /\s(?=\S*$)/]) {
    const m = window.match(re);
    if (m?.index !== undefined) return m.index + m[0].length;
  }
  return limit;
}
