/**
 * Messages the assistant participant sends to the browser over the LiveKit
 * data channel.
 *
 * One transport for the whole screen: no polling, no second connection. The
 * browser renders what it is told and guesses nothing — in particular the call
 * bar reflects server phase rather than local inference, because the server is
 * the only party that knows whether it is still generating.
 */

import type { CallPhase, FieldState } from './types.js';

export interface SessionBootstrap {
  sessionId: string;
  roomName: string;
  clinician: { name: string; initials: string; practice: string; context: string };
  patientFirstName: string;
  fields: FieldState[];
}

export type ServerMessage =
  /** Append a bubble to the thread. */
  | { t: 'utterance'; who: 'assistant' | 'patient'; text: string; id: string }
  /** Drive the call bar. */
  | { t: 'phase'; phase: CallPhase }
  /** Repaint the "Notes so far" card. */
  | { t: 'notes'; fields: FieldState[] }
  /** The line closes on a sentence, not a submit. */
  | { t: 'ended'; reason: 'complete' | 'safety' | 'error'; say?: string };

export type ClientMessage =
  /** The patient chose to type rather than speak. */
  | { t: 'typed'; text: string };

export const encodeMessage = (m: ServerMessage | ClientMessage): Uint8Array =>
  new TextEncoder().encode(JSON.stringify(m));

export const decodeMessage = <T>(d: Uint8Array): T =>
  JSON.parse(new TextDecoder().decode(d)) as T;
