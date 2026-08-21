import type { QueuedInterview } from '@metafora/contracts';

/**
 * The dispatch queue.
 *
 * The app is dispatched from a queue. For now the queue is this array and
 * `POST /session` takes the head. Swapping it for a real queue — Vercel
 * Queues, a database table, a message broker — touches this file and nothing
 * else, which is the point of putting it behind a function rather than
 * inlining the patient into the session route.
 */
const QUEUE: QueuedInterview[] = [
  {
    id: 'iv_0001',
    protocolId: 'proto_warmup_v1',
    patient: { id: 'pt_alice', firstName: 'Alice' },
  },
];

/** Peek at the next interview without consuming it (re-runnable in dev). */
export function nextInterview(): QueuedInterview {
  const head = QUEUE[0];
  if (!head) throw new Error('dispatch queue is empty');
  return head;
}
