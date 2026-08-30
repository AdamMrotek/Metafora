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
  /** The patient's identity, as `clinical.patients` holds it. Null for a row */
  /** this product created itself — the intake asks for a first name, and a */
  /** real deployment carries the rest across at dispatch. On the summary */
  /** rather than behind a detail fetch because the review table draws a */
  /** masked NHS number on every line. */
  patientNhsNumber: string | null;
  patientDateOfBirth: string | null;
  patientOrigin: PatientOrigin;
  protocolId: string;
  protocolLabel: string;
  /** How much of the script the call actually got through. `captured_fields` */
  /** is counted from `clinical.results`; `total_fields` from the questions the */
  /** pinned protocol declares, because a call that has not ended yet has no */
  /** result rows and 0/0 would read as an empty script. On the row rather than */
  /** behind a second request because the review table draws a progress meter */
  /** per line, and one lateral join is cheaper than a detail fetch per row. */
  capturedFields: number;
  totalFields: number;
  /** How many distinct red flags the gate matched across the whole call, and */
  /** the most serious action any of them carried. Null and zero mean the same */
  /** thing said twice: the gate ran on every turn and nothing tripped it. */
  /**  */
  /** `outcome` only distinguishes the flag that *stopped* the call */
  /** (`end_call` → `safety`). An `urgent_escalate` or a `soft_review` lets the */
  /** conversation finish, so without these two fields a flagged call and a */
  /** clean one are the same row, and the clinician's own summary cannot tell */
  /** them apart. That is the number the dashboard counts. */
  flagCount: number;
  worstFlag: RedFlagAction | null;
  scheduledFor: string | null;
  startedAt: string | null;
  endedAt: string | null;
  createdAt: string;
}

/**
 * One entry of the composer's *what* field.
 *
 * The id and the label only. A composer that had to fetch whole
 * `ProtocolVersion`s to fill a select would be pulling every question of every
 * protocol across to draw four words.
 */
export interface ProtocolOption {
  id: string;
  label: string;
}

/**
 * One page of the review table, and enough to draw the pager under it.
 *
 * The rows used to be the whole list: `GET /interviews` returned the hundred
 * most recent and the browser sorted, searched and paged them. That is fine at
 * twenty interviews and wrong at a hundred and one, because the browser would
 * be triaging a window it cannot see past — so `total` is here, counted in the
 * same statement as the rows, and it is the number that says whether anything
 * was left out.
 */
export interface InterviewPage {
  rows: InterviewSummary[];
  /** Rows matching the filter across the caller's whole scope, not on this page. */
  total: number;
  page: number;
  /** Always at least 1, so an empty table still reads "page 1 of 1" rather */
  /** than "page 1 of 0". */
  pages: number;
}

/**
 * Everything on the dashboard that is not one page of the table.
 *
 * It exists because the table stopped being the whole list. Every one of these
 * was computed in the browser from the same fetch the table used, which was
 * workable only while that fetch was the whole record — the moment it became
 * one page of eight, a tile counting it would have been counting a page and
 * calling it the caseload.
 */
export interface Overview {
  /** The three tiles. Three errands, not a census: what the gate stopped, what */
  /** it flagged on a call that ran on, and what neither happened to but which */
  /** stopped short of its script. */
  urgent: number;
  flagged: number;
  incomplete: number;
  /** Open escalations, newest first, for the band above every screen. Capped — */
  /** `urgent` is the count it states. */
  escalations: InterviewSummary[];
  /** Calls still out, soonest first: the scheduled card and the Deployments */
  /** screen's upcoming table are the same rows asked twice. */
  queued: InterviewSummary[];
  /** What the protocol filter may offer — the protocols present in this */
  /** caller's record, never every protocol that exists. A filter listing an */
  /** option that selects nothing is a filter that lies about the record. */
  protocols: ProtocolOption[];
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
  /** Every interview for this patient, oldest first, including this one — the */
  /** detail screen's timeline. Fetched with the thing it is a timeline of, */
  /** rather than filtered out of the dashboard's list, so opening an interview */
  /** directly from a bookmark draws the same screen as reaching it from the */
  /** table. */
  history: InterviewSummary[];
}

/**
 * The caller, as `config.accounts` has them — what `GET /me` returns.
 *
 * The dashboard greets a person by name and shows what their role admits, and
 * both facts live in a table only the server can read. Without this the
 * greeting is an email address and the UI guesses at the role, which is the
 * kind of guess that becomes a client-side authorisation check.
 */
export interface Account {
  email: string;
  role: string;
  displayName: string;
}

/**
 * What a browser needs before it can sign anybody in — `GET /config`.
 *
 * Handed down rather than baked into a bundle, which is the same argument
 * `POST /session` already makes for `LIVEKIT_PUBLIC_URL`: the frontend holds
 * one piece of configuration (where to send `/api`), and rotating the publishable key
 * is a secret change rather than a rebuild of two static sites.
 *
 * Both values are public by construction — the publishable key is published to every
 * browser that signs in, and `config.accounts` is what decides who has a
 * caseload.
 */
export interface PublicConfig {
  supabaseUrl: string;
  supabasePublishableKey: string;
}

/**
 * One row of the patients screen: the caller's own list, plus the
 * unowned demo rows nobody was dispatched.
 */
export interface PatientSummary {
  id: string;
  firstName: string;
  /** Seeded, and only ever from the range NHS England reserves for test data — */
  /** `clinical.patients` has a CHECK that will not hold anything else. Null for */
  /** a row this product created itself. */
  nhsNumber: string | null;
  dateOfBirth: string | null;
  origin: PatientOrigin;
  /** Null for a demo visitor — nobody was dispatched a call to them. */
  clinicianEmail: string | null;
  interviewCount: number;
  /** When something last happened to them — the end of their last call, or its */
  /** start, or when it was queued. Null for a patient nobody has called. */
  lastInterviewAt: string | null;
  /** Their last finished call, which is what the row's arrow opens and what */
  /** its middle column names. All three null together. */
  lastInterviewId: string | null;
  lastProtocolId: string | null;
  lastProtocolLabel: string | null;
  /** The slot on their next call still out, if one is. */
  nextScheduledFor: string | null;
  /** What is still owed on them: calls the gate stopped, and calls that did */
  /** not finish. Counted server-side because a count taken from one page of */
  /** the review table changes when you turn the page. */
  openCount: number;
  /** Whether any of those was the gate stopping a call, which is what decides */
  /** the pill is red rather than amber. */
  hasEscalation: boolean;
  createdAt: string;
}

/**
 * Queue one interview — the composer's *who · what · when*.
 *
 * Exactly one of `patient_id` and `first_name`: an existing person off the
 * caller's list, or a new one. A new patient is a first name and nothing else,
 * because this product collects no demographics and `docs/system-map.md`
 * forbids inventing any — the identity a real deployment holds is carried
 * across at dispatch from a system that already has it.
 */
export interface DispatchRequest {
  /** Someone already on the caller's list. Must be within their scope, or the */
  /** request is a 404 for the same reason a read outside it is. */
  patientId?: string;
  /** A person nobody has called before. First name only, and it is what the */
  /** assistant says out loud in the first sentence of the call. */
  firstName?: string;
  protocolId: string;
  /** When the call is for. Null means "as soon as they follow the link" — the */
  /** row is queued either way, because nothing here dials anybody. */
  scheduledFor?: string;
}

/**
 * A link, as the dashboard copies it.
 *
 * The URL is assembled by the backend, not the browser, for the same reason
 * `POST /session` hands down `LIVEKIT_PUBLIC_URL`: where the patient portal
 * lives is deployment configuration, and a bundle that baked it in would have
 * to be rebuilt to move it.
 */
export interface Invitation {
  url: string;
  interviewId: string;
  channel: InvitationChannel;
  expiresAt: string | null;
}

/**
 * The optional body of `POST /session`.
 *
 * Every field defaults, so the body-less POST the public demo has always made
 * still means "start a demo call" and nothing about the patient portal's
 * existing request had to change.
 */
export interface SessionStart {
  /** The opaque token out of `?invite=`. Never an interview id: the id is on */
  /** the clinician's screen and in the dashboard's URLs, and a link that */
  /** carried it would let anyone who saw one guess the next. */
  invite?: string;
}

/** One bar of the chart. */
export interface ExperienceDay {
  /** Already formatted for the axis, because the window is chosen server-side */
  /** and the browser has no way to know whether it is drawing weekdays or */
  /** dates without being told twice. */
  label: string;
  positive: number;
  neutral: number;
  negative: number;
}

/**
 * The panel, in one request.
 *
 * `scope` is the caption under the chart and it names dates rather than saying
 * "today", because the window anchors on the newest response in the table
 * rather than on the clock — seeded data goes stale, and a chart that claims
 * to be today when it is drawing last spring is the lie this whole stage was
 * about removing.
 */
export interface ExperienceSummary {
  days: ExperienceDay[];
  scope: string;
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

export type InterviewSort = 'urgency' | 'recent';

export type InvitationChannel = 'link' | 'email';

export type ExperienceRange = 'today' | 'week' | 'all';
