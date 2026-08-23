/*
 * GENERATED FILE — do not edit by hand.
 *
 * Generated from the pydantic models by `scripts/gen_contracts.py`.
 * pydantic is the single source of truth; run the generator and commit
 * the result. `tests/test_contracts_generated.py` fails when this file
 * is stale.
 */


import type { Clinician, FieldState, CallPhase } from './types.js';

/** Returned by `POST /session`, before the browser joins the room. */
export interface SessionBootstrap {
  sessionId: string;
  roomName: string;
  clinician: Clinician;
  patientFirstName: string;
  fields: FieldState[];
}

/** Append a bubble to the thread. */
export interface UtteranceMessage {
  t: 'utterance';
  who: 'assistant' | 'patient';
  text: string;
  id: string;
}

/** Drive the call bar. */
export interface PhaseMessage {
  t: 'phase';
  phase: CallPhase;
}

/** Repaint the "Notes so far" card. */
export interface NotesMessage {
  t: 'notes';
  fields: FieldState[];
}

/** The line closes on a sentence, not a submit. */
export interface EndedMessage {
  t: 'ended';
  reason: 'complete' | 'safety' | 'error';
  say?: string;
}

/** The patient chose to type rather than speak. */
export interface TypedMessage {
  t: 'typed';
  text: string;
}

export type ServerMessage =
  | { t: 'utterance'; who: 'assistant' | 'patient'; text: string; id: string }
  | { t: 'phase'; phase: CallPhase }
  | { t: 'notes'; fields: FieldState[] }
  | { t: 'ended'; reason: 'complete' | 'safety' | 'error'; say?: string };

export type ClientMessage =
  | { t: 'typed'; text: string };

export const encodeMessage = (m: ServerMessage | ClientMessage): Uint8Array =>
  new TextEncoder().encode(JSON.stringify(m));

export const decodeMessage = <T>(d: Uint8Array): T =>
  JSON.parse(new TextDecoder().decode(d)) as T;
