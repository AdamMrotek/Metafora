import { randomUUID } from 'node:crypto';
import type { ProtocolVersion, QueuedInterview } from '@metafora/contracts';
import type { ChatMessage } from '../inference/types.ts';
import { createSessionLog, type SessionLog } from './log.ts';
import { InterviewMachine } from './machine.ts';

/**
 * Session records, held in this process.
 *
 * The backend owns conversation state. It owns it by being *in* the media
 * path rather than by reacting to what a browser chose to relay — the history
 * below is written from the loop, and nothing outside this process can append
 * to it.
 *
 * In-memory is honest for phase 1: this process is already stateful because it
 * holds live WebRTC connections, so persisting the record without also solving
 * mid-call restart would buy nothing. The log on disk is the durable artefact.
 */

export interface Session {
  id: string;
  roomName: string;
  protocol: ProtocolVersion;
  interview: QueuedInterview;
  machine: InterviewMachine;
  log: SessionLog;
  /** Both sides of the conversation, as the model sees it. */
  history: ChatMessage[];
  startedAt: number;
  endedAt: number | null;
  endedReason: string | null;
}

const sessions = new Map<string, Session>();

export function createSession(interview: QueuedInterview, protocol: ProtocolVersion): Session {
  const id = `s_${randomUUID().slice(0, 8)}`;
  const machine = new InterviewMachine(protocol);
  const log = createSessionLog(id);

  const session: Session = {
    id,
    roomName: `intake-${id}`,
    protocol,
    interview,
    machine,
    log,
    history: [{ role: 'system', content: systemPrompt(protocol, interview) }],
    startedAt: Date.now(),
    endedAt: null,
    endedReason: null,
  };

  sessions.set(id, session);
  log.append({
    type: 'session.created',
    protocolId: protocol.id,
    patientId: interview.patient.id,
    roomName: session.roomName,
  });
  return session;
}

export const getSession = (id: string) => sessions.get(id);

export function endSession(session: Session, reason: string) {
  if (session.endedAt) return;
  session.endedAt = Date.now();
  session.endedReason = reason;
  session.log.append({ type: 'session.ended', reason, fields: session.machine.captured });
}

/**
 * The system prompt carries Block I and nothing else.
 *
 * The safety break, the escalation routes and the tool matrix are deliberately
 * absent: they are enforced in code, before and around generation. Restating
 * them here as prose would imply the model is what makes them hold, which is
 * exactly how they stop being enforceable.
 *
 * Nothing per-patient beyond the first name enters it, so the cache prefix
 * stays stable across every interview running this protocol.
 */
function systemPrompt(protocol: ProtocolVersion, interview: QueuedInterview): string {
  const questions = protocol.script.sections
    .flatMap((s) => s.questions.map((q) => `- ${q.ask} (records: ${q.fieldKey})`))
    .join('\n');

  return [
    `You are a clinical intake assistant calling ${interview.patient.firstName} on behalf of ${protocol.clinician.name} at ${protocol.clinician.practice}.`,
    `This is ${protocol.clinician.context}.`,
    '',
    'You are speaking out loud on a phone call. Therefore:',
    '- Keep every reply to one or two short sentences. Never list, never enumerate.',
    '- Write words as they are said. No markdown, no bullet points, no emoji, no stage directions.',
    '- Sound like a person, not a form. Acknowledge what they said before moving on.',
    '- The patient can interrupt you at any time. If they do, follow them.',
    '',
    'The questions to get through, in order:',
    questions,
    '',
    'Ask them one at a time and in your own words. As soon as the patient has answered one,',
    'call update_intake to record it, using their own words where you can. If an answer is',
    'unclear, ask once more; if it is still unclear, record what they did say and move on.',
    'When every question is answered, say goodbye warmly and stop.',
    '',
    'You are not a clinician. Do not diagnose, do not advise, and do not interpret symptoms.',
    'If asked for medical advice, say the practice will go through it with them.',
  ].join('\n');
}
