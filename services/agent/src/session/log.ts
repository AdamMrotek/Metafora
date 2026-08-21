import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The session log.
 *
 * Because every transcript and every reply passes through this process, the
 * log is written *from the source* rather than reconstructed from whatever the
 * browser chose to relay. That is the audit trail: both sides of the
 * conversation, every state transition, every red-flag decision, and the
 * latency of every turn.
 *
 * Append-only JSONL keeps it greppable and defers the database decision. The
 * interface is the point — swapping in SQLite or Postgres later is one new
 * implementation, not a migration of the loop.
 *
 * Raw audio is deliberately not retained: transcripts and state transitions
 * only. Whether that changes is a DPIA decision, and not retaining is the
 * reversible default.
 */

export type LogEvent =
  | { type: 'session.created'; protocolId: string; patientId: string; roomName: string }
  | { type: 'room.joined'; identity: string }
  | { type: 'patient.joined'; identity: string }
  | { type: 'turn.committed'; transcript: string; durationMs: number; source: 'voice' | 'typed' }
  | { type: 'opening.spoken'; text: string; chunks: number; ms: number }
  | { type: 'safety.scanned'; blocked: boolean; hits: string[]; action?: string }
  | { type: 'llm.completed'; text: string; toolCalls: number }
  | { type: 'tool.called'; name: string; args: unknown; authorised: boolean; reason?: string }
  | { type: 'tts.spoken'; chars: number; chunks: number }
  | { type: 'state.transition'; from: string; to: string }
  | { type: 'turn.aborted'; reason: 'barge_in'; discardedText: string }
  | { type: 'latency.turn'; ms: Record<string, number> }
  | { type: 'error'; where: string; message: string }
  | { type: 'session.ended'; reason: string; fields: Record<string, string | null> };

export interface SessionLog {
  append(event: LogEvent): void;
}

const LOG_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../logs');

export function createSessionLog(sessionId: string): SessionLog {
  mkdirSync(LOG_DIR, { recursive: true });
  const file = resolve(LOG_DIR, `${sessionId}.jsonl`);

  return {
    append(event) {
      const line = JSON.stringify({ at: new Date().toISOString(), sessionId, ...event });
      try {
        appendFileSync(file, `${line}\n`);
      } catch (err) {
        // A failed write must never take down a live call, but it must be loud.
        console.error('[log] write failed', err);
      }
      console.log(`[${sessionId}] ${event.type}`, summarise(event));
    },
  };
}

function summarise(e: LogEvent): string {
  switch (e.type) {
    case 'turn.committed':
      return `"${e.transcript}"`;
    case 'opening.spoken':
      return `${e.chunks} chunks in ${e.ms}ms`;
    case 'safety.scanned':
      return e.blocked ? `BLOCKED ${e.hits.join(',')}` : 'clean';
    case 'llm.completed':
      return `"${e.text.slice(0, 60)}"${e.toolCalls ? ` +${e.toolCalls} tool` : ''}`;
    case 'tool.called':
      return `${e.name} ${e.authorised ? 'ok' : `REFUSED (${e.reason})`}`;
    case 'latency.turn':
      return Object.entries(e.ms).map(([k, v]) => `${k}=${v}ms`).join(' ');
    case 'turn.aborted':
      return `discarded "${e.discardedText.slice(0, 40)}"`;
    case 'error':
      return `${e.where}: ${e.message}`;
    default:
      return '';
  }
}
