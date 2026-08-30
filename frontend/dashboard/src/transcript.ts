import type { TranscriptEvent } from '@metafora/contracts';

/**
 * `transcript.events` → the two-column transcript the spec draws.
 *
 * The log is not a chat history; it is everything the pipeline did, and three
 * of its fifteen event types carry words:
 *
 *   `opening.spoken`  the sentence the assistant opens with
 *   `llm.completed`   what the model generated  — `tts.spoken` carries only
 *                     `chars` and `chunks`, so the text has to come from here
 *   `turn.committed`  what the patient actually said, once endpointing decided
 *                     they had finished saying it
 *
 * `safety.scanned` runs on the committed turn *before* any generation, so it
 * attaches to the patient line above it rather than standing on its own. Every
 * one of them is rendered, including the ones that matched nothing: a scan that
 * cleared a turn is the evidence the gate ran, and it is the only thing on this
 * screen that can show what was looked for and not found.
 */

export type Line = {
  seq: number;
  at: string;
  who: string;
  text: string;
  scan?: { text: string; hit: boolean };
};

type Payload = Record<string, unknown>;

const str = (payload: Payload, key: string): string =>
  typeof payload[key] === 'string' ? (payload[key] as string) : '';

export function lines(events: TranscriptEvent[]): Line[] {
  const out: Line[] = [];

  for (const event of events) {
    const payload = event.payload as Payload;

    if (event.type === 'opening.spoken' || event.type === 'llm.completed') {
      const text = str(payload, 'text');
      if (text) out.push({ seq: event.seq, at: event.at, who: 'Assistant', text });
      continue;
    }

    if (event.type === 'turn.committed') {
      const text = str(payload, 'transcript');
      const source = str(payload, 'source');
      if (text) {
        out.push({
          seq: event.seq,
          at: event.at,
          who: source === 'typed' ? 'Patient · typed' : 'Patient',
          text,
        });
      }
      continue;
    }

    if (event.type === 'safety.scanned') {
      const hits = Array.isArray(payload.hits) ? (payload.hits as string[]) : [];
      const action = str(payload, 'action');
      const scan = {
        hit: hits.length > 0,
        text: hits.length
          ? `scan ran · matched ${hits.join(', ')}${action ? ` · ${action}` : ''}`
          : 'scan ran · matched nothing',
      };
      // Onto the turn it ran on. If there is no turn above it — nothing has
      // been said yet — it stands alone rather than being dropped, because a
      // scan we cannot place is still a scan that happened.
      const last = out[out.length - 1];
      if (last && last.who.startsWith('Patient') && !last.scan) last.scan = scan;
      else out.push({ seq: event.seq, at: event.at, who: 'Safety', text: '', scan });
    }
  }

  return out;
}
