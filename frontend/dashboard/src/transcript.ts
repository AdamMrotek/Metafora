import type { TranscriptEvent } from '@metafora/contracts';

/**
 * `transcript.events` → the two-column transcript the spec draws.
 *
 * The log is not a chat history; it is everything the pipeline did, and four
 * of its seventeen event types carry words:
 *
 *   `opening.spoken`  the sentence the assistant opens with
 *   `closure.spoken`  the authored sentence a stopped call ends on. Neither
 *                     bookend is generated, so neither reaches `llm.completed`
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
 *
 * `concern.raised` is the other net and is drawn as its own line rather than
 * folded into the patient's. It happens *after* the answer, not before it, and
 * what raised it is a judgement about that answer rather than a phrase in it —
 * so a reader has to be able to tell the two apart at a glance. That is the
 * whole reason it is a separate event type and not a second `safety.scanned`.
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

    if (
      event.type === 'opening.spoken' ||
      event.type === 'closure.spoken' ||
      event.type === 'llm.completed'
    ) {
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
      continue;
    }

    if (event.type === 'concern.raised') {
      const hits = Array.isArray(payload.hits) ? (payload.hits as string[]) : [];
      const judged = Array.isArray(payload.judged) ? (payload.judged as string[]) : [];
      const action = str(payload, 'action');
      const field = str(payload, 'field');
      // Which net caught it, named. A clinician deciding what to do about a
      // flag is owed the difference between one the protocol looked up and one
      // a model proposed, and this line is the only place that difference is
      // legible.
      const how = hits.length
        ? hits.map((id) => (judged.includes(id) ? `${id} (judged)` : id)).join(', ')
        : '';
      out.push({
        seq: event.seq,
        at: event.at,
        who: 'Review',
        text: '',
        scan: {
          hit: hits.length > 0,
          text: hits.length
            ? `${field} reviewed · raised ${how}${action ? ` · ${action}` : ''}`
            : `${field} reviewed · raised nothing`,
        },
      });
    }
  }

  return out;
}
