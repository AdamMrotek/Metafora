/*
 * GENERATED FILE — do not edit by hand.
 *
 * Generated from the pydantic models by `scripts/gen_contracts.py`.
 * pydantic is the single source of truth; run the generator and commit
 * the result. `tests/test_contracts_generated.py` fails when this file
 * is stale.
 */


export interface Question {
  id: string;
  /** Spoken to the patient. The only block a patient hears in full. */
  ask: string;
  /** Where the answer lands in the record, and on the clinician's composer. */
  fieldKey: string;
  /** Human label for the "Notes so far" card and the review composer row. */
  label: string;
  capture: Capture;
  /** What to do when the answer does not resolve. Drives the follow-up budget. */
  ifUnclear?: string;
  /** A section may be skipped by policy; a question may not. */
  mustCapture: boolean;
}

export interface Section {
  id: string;
  title: string;
  questions: Question[];
}

export interface InterviewScript {
  sections: Section[];
}

/**
 * Runs on the committed turn, before any generation — so the model is not in
 * the loop by construction. Every entry carries the utterance that must trip
 * it, which is what makes the fixture generatable.
 */
export interface RedFlag {
  id: string;
  label: string;
  /** Deterministic match. Compiled case-insensitively against the transcript. */
  patterns: string[];
  /** Only one outcome stops the conversation. */
  action: RedFlagAction;
  /** The sentence spoken to the patient when this trips, if the call ends. */
  say?: string;
  /** The utterance that proves it fires. Generates the suite fixture. */
  provingUtterance: string;
}

export interface UrgentEscalation {
  /** A rota with a timeout, never a person. */
  rota: string[];
  timeoutMinutes: number;
}

export interface SoftReviewTrigger {
  id: string;
  reason: string;
}

/** A matrix, not a list: what it may call, from which states, how many times. */
export interface ToolSpec {
  name: string;
  description: string;
  /** Fully-qualified state ids (`sectionId.questionId`) this tool is legal in. */
  allowedStates: string[];
  maxAttemptsPerTurn: number;
}

export interface ReportGuidance {
  /** Field keys the review composer renders, in this order. */
  fields: string[];
  rules: string[];
}

export interface Clinician {
  name: string;
  initials: string;
  practice: string;
  /** The line under the name: why this call is happening. */
  context: string;
}

/**
 * One immutable ProtocolVersion. Every interview pins one, so a report that
 * says "severity 7" can always be read back against the question that was asked.
 */
export interface ProtocolVersion {
  id: string;
  label: string;
  frozen: true;
  clinician: Clinician;
  script: InterviewScript;
  redFlags: RedFlag[];
  urgent: UrgentEscalation | null;
  softReview: SoftReviewTrigger[];
  tools: ToolSpec[];
  report: ReportGuidance;
}

export interface Patient {
  id: string;
  firstName: string;
}

/** A dispatch-queue entry. In production this comes off a queue; for now, one. */
export interface QueuedInterview {
  id: string;
  protocolId: string;
  patient: Patient;
}

/** One line of the "Notes so far" card, and one field on the review composer. */
export interface FieldState {
  key: string;
  label: string;
  value: string | null;
  status: FieldStatus;
}

/** One row of the review table. */
export interface InterviewSummary {
  id: string;
  status: InterviewStatus;
  /** How the call ended, in the store's vocabulary — `complete`, `safety`, */
  /** `patient_left`. Null until it has ended. */
  outcome: string | null;
  patientId: string;
  patientFirstName: string;
  patientOrigin: PatientOrigin;
  protocolId: string;
  protocolLabel: string;
  scheduledFor: string | null;
  startedAt: string | null;
  endedAt: string | null;
  createdAt: string;
}

/** One captured field, as the review composer renders it. */
export interface ResultField {
  fieldKey: string;
  label: string;
  value: string | null;
  status: FieldStatus;
  updatedAt: string;
}

/**
 * One line of `transcript.events`, as the writer appended it.
 *
 * `payload` is the whole event, unflattened, because the union of event shapes
 * lives in `services/agent/session_log.py` and re-declaring fifteen variants
 * here to serve one screen would be two sources of truth for the same record.
 */
export interface TranscriptEvent {
  seq: number;
  type: string;
  at: string;
  payload: Record<string, unknown>;
}

/**
 * One interview, everything about it, in one request.
 *
 * The transcript carries **every** safety scan, including the ones that
 * matched nothing. A route that filtered them would look identical and would
 * quietly remove the only evidence that the gate ran on a turn it cleared.
 */
export interface InterviewDetail {
  interview: InterviewSummary;
  results: ResultField[];
  events: TranscriptEvent[];
}

/**
 * One row of the patients screen: the caller's own list, plus the
 * unowned demo rows nobody was dispatched.
 */
export interface PatientSummary {
  id: string;
  firstName: string;
  origin: PatientOrigin;
  /** Null for a demo visitor — nobody was dispatched a call to them. */
  clinicianEmail: string | null;
  interviewCount: number;
  lastInterviewAt: string | null;
  createdAt: string;
}

export type Capture =
  | { type: 'text' }
  | { type: 'enum'; values: string[] }
  | { type: 'number'; unit?: string }
  | { type: 'boolean' }
  | { type: 'date' };

export type RedFlagAction = 'end_call' | 'urgent_escalate' | 'soft_review' | 'note_only';

export type FieldStatus = 'pending' | 'live' | 'open' | 'captured';

export type CallPhase = 'idle' | 'listening' | 'thinking' | 'speaking' | 'ended';

export type InterviewStatus = 'queued' | 'running' | 'completed' | 'abandoned';

export type PatientOrigin = 'demo' | 'dispatched';
