/**
 * Strips reasoning out of a token stream, before anything downstream sees it.
 *
 * gpt-oss is a reasoning model, and in this product the model's reply is *read
 * aloud to a patient*. `reasoning_format: 'hidden'` is the primary defence and
 * this is the second one.
 *
 * It has to work on the stream rather than on finished text, because the
 * sentence chunker cuts the reply into TTS-sized pieces as it arrives: a
 * `<think>` opened in one chunk and closed in another never matches a
 * whole-block regex, and the reasoning is spoken. That is not a hypothetical —
 * it is what this class was written in response to.
 *
 * Tags split across deltas are handled by holding back any trailing text that
 * could still turn out to be the start of one.
 */

const OPEN = '<think>';
const CLOSE = '</think>';

export class ReasoningFilter {
  private inside = false;
  private buffer = '';

  /** Feed a delta; get back only what is safe to say out loud. */
  push(delta: string): string {
    this.buffer += delta;
    let out = '';

    for (;;) {
      if (!this.inside) {
        const open = this.buffer.indexOf(OPEN);
        if (open === -1) {
          const hold = partialTagSuffix(this.buffer);
          out += this.buffer.slice(0, this.buffer.length - hold);
          this.buffer = this.buffer.slice(this.buffer.length - hold);
          break;
        }
        out += this.buffer.slice(0, open);
        this.buffer = this.buffer.slice(open + OPEN.length);
        this.inside = true;
      } else {
        const close = this.buffer.indexOf(CLOSE);
        if (close === -1) {
          // Everything here is reasoning. Keep only what might be a partial
          // closing tag and discard the rest unsaid.
          this.buffer = this.buffer.slice(this.buffer.length - partialTagSuffix(this.buffer));
          break;
        }
        this.buffer = this.buffer.slice(close + CLOSE.length);
        this.inside = false;
      }
    }
    return out;
  }

  /**
   * End of stream. An unterminated `<think>` means the model was still
   * reasoning when it stopped, so the tail is dropped rather than spoken.
   */
  flush(): string {
    const rest = this.inside ? '' : this.buffer;
    this.buffer = '';
    this.inside = false;
    return rest;
  }
}

/** Length of the longest suffix that could still become a tag. */
function partialTagSuffix(text: string): number {
  const max = Math.min(text.length, CLOSE.length - 1);
  for (let n = max; n > 0; n--) {
    const tail = text.slice(text.length - n);
    if (OPEN.startsWith(tail) || CLOSE.startsWith(tail)) return n;
  }
  return 0;
}
