/**
 * The artefact that travels between all three layers of Metafora.
 *
 * `frontend/studio/docs/ux-agent-studio.html` defines it: six blocks, frozen at
 * publish, never
 * edited in place. The studio authors it, the evals runner proves it, and the
 * clinician portal deploys it to a patient. This file is the contract.
 *
 * Note the studio's own warning: four of the six blocks never enter the prompt
 * at all. The break is a gate that runs before generation, the routes are
 * policy, the tool table is a permission matrix enforced in-process. Writing
 * them as prose in a system message is exactly how they stop being enforceable.
 */

// ─── Block I · interview script ──────────────────────────────────────────────

/** How an answer is captured into the record. */
export type Capture =
  | { type: 'text' }
  | { type: 'enum'; values: string[] }
  | { type: 'number'; unit?: string }
  | { type: 'boolean' }
  | { type: 'date' };

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

// ─── Block II · safety break ─────────────────────────────────────────────────

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
  action: 'end_call' | 'urgent_escalate' | 'soft_review' | 'note_only';
  /** The sentence spoken to the patient when this trips, if the call ends. */
  say?: string;
  /** The utterance that proves it fires. Generates the suite fixture. */
  provingUtterance: string;
}

// ─── Blocks III & IV · escalation ────────────────────────────────────────────

export interface UrgentEscalation {
  /** A rota with a timeout, never a person. */
  rota: string[];
  timeoutMinutes: number;
}

export interface SoftReviewTrigger {
  id: string;
  reason: string;
}

// ─── Block V · tools ─────────────────────────────────────────────────────────

/** A matrix, not a list: what it may call, from which states, how many times. */
export interface ToolSpec {
  name: string;
  description: string;
  /** Fully-qualified state ids (`sectionId.questionId`) this tool is legal in. */
  allowedStates: string[];
  maxAttemptsPerTurn: number;
}

// ─── Block VI · report guidance ──────────────────────────────────────────────

export interface ReportGuidance {
  /** Field keys the review composer renders, in this order. */
  fields: string[];
  rules: string[];
}

// ─── The published version ───────────────────────────────────────────────────

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

// ─── Runtime records ─────────────────────────────────────────────────────────

/** A dispatch-queue entry. In production this comes off a queue; for now, one. */
export interface QueuedInterview {
  id: string;
  protocolId: string;
  patient: { id: string; firstName: string };
}

export type FieldStatus = 'pending' | 'live' | 'open' | 'captured';

/** One line of the "Notes so far" card, and one field on the review composer. */
export interface FieldState {
  key: string;
  label: string;
  value: string | null;
  status: FieldStatus;
}

export type CallPhase = 'idle' | 'listening' | 'thinking' | 'speaking' | 'ended';
