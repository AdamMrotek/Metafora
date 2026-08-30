import type { InterviewSummary, RedFlagAction } from '@metafora/contracts';

/**
 * Turning rows into the sentences the spec writes.
 *
 * The review table says "Abandoned after 4 minutes · stopped mid-answer on pain
 * severity", not "abandoned / patient_left". Everything here is that
 * translation, in one place, because the same vocabulary appears on four
 * screens and a table that disagrees with a pill is a table nobody trusts.
 */

const DATE = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short' });
const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
const LONG = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });

export const stamp = (iso: string): string =>
  `${DATE.format(new Date(iso))} ${TIME.format(new Date(iso))}`;

export const today = (): string => LONG.format(new Date());

export function greeting(at: Date = new Date()): string {
  const hour = at.getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/** "6 min ago", "yesterday", "17 Aug". */
export function relative(iso: string, now: Date = new Date()): string {
  const seconds = (now.getTime() - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} hours ago`;
  if (seconds < 172_800) return 'yesterday';
  if (seconds < 604_800) return `${Math.round(seconds / 86_400)} days ago`;
  return DATE.format(new Date(iso));
}

/** How long something has been owed — the rail's clock, `mm:ss` past an hour. */
export function elapsed(iso: string, now: Date = new Date()): string {
  const total = Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return hours ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
}

/**
 * The gap between two contacts, as the timeline's spine labels it — or null
 * when there is no interval to label.
 *
 * Days were the only unit, so two calls the same day read `0d`: a label that
 * breaks the spine to say nothing happened between them, which is exactly what
 * a duration is not. Below a day it now says the duration it actually was, and
 * two calls inside a minute of each other get no label at all rather than a
 * rounded-down zero.
 */
export function gap(fromIso: string, toIso: string): string | null {
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime();
  const mins = Math.round(ms / 60_000);
  if (mins < 1) return null;
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(ms / 86_400_000);
  if (days < 14) return `${days}d`;
  if (days < 60) return `${Math.round(days / 7)}w`;
  return `${Math.round(days / 30)}mo`;
}

function minutes(row: InterviewSummary): number | null {
  if (!row.startedAt || !row.endedAt) return null;
  return Math.max(
    1,
    Math.round((new Date(row.endedAt).getTime() - new Date(row.startedAt).getTime()) / 60_000),
  );
}

export type Outcome = { title: string; detail: string; urgent: boolean };

/** What each action means to the person reading the table, rather than to the
 *  gate that raised it. `end_call` is the only one that stops a call, and the
 *  row it belongs to says so in its own words already. */
const FLAG_WORDS: Record<RedFlagAction, string> = {
  end_call: 'the call was stopped',
  urgent_escalate: 'urgent escalation',
  soft_review: 'flagged for review',
  note_only: 'noted for the record',
};

/**
 * Which of those actions is *red* — the same set `reads.RED_ACTIONS` derives
 * from `SEVERITY`, restated here because the ladder is Python and never crossed
 * into `shared/contracts`.
 *
 * Written as an exhaustive `Record` rather than an array so a re-authored flag
 * set cannot add an action that silently lands on the wrong side of the line:
 * `tsc` will not compile the new action until this file says which it is.
 */
const RED: Record<RedFlagAction, boolean> = {
  end_call: true,
  urgent_escalate: true,
  soft_review: false,
  note_only: false,
};

/**
 * Whether this row is one of the reds the band and the `urgent` tile count.
 *
 * Red is the flag's **action**, never the outcome. `outcome === 'safety'` is
 * only the flag that *stopped* the call; an `urgent_escalate` lets the
 * conversation run to the end, so the row arrives here as
 * `completed / complete` and every branch below that asked `outcome` alone
 * drew it yellow — while `reads.py` had already been moved onto
 * `UNACKNOWLEDGED_RED` and was counting it red. The band said red, the screen
 * it linked to said flagged, and they were describing the same call.
 */
export function isRed(row: InterviewSummary): boolean {
  return row.worstFlag !== null && RED[row.worstFlag];
}

/**
 * The gate's finding, as a clause — or null when it found nothing, which is
 * most calls and is itself the useful answer.
 *
 * A flag that did not end the call leaves no trace in `status` or `outcome`, so
 * before this the row for a patient who said they were still on their
 * anticoagulant and the row for a patient who raised nothing were the same row.
 */
export function flags(row: InterviewSummary): string | null {
  if (!row.flagCount || !row.worstFlag) return null;
  const n = row.flagCount === 1 ? '1 red flag' : `${row.flagCount} red flags`;
  return `${n} · ${FLAG_WORDS[row.worstFlag]}`;
}

/** The sentence, and then what the gate found appended to it. Split in two so
 *  every branch below picks up the flag clause without restating it — an
 *  urgent flag on a call that ran to the end is exactly the case a branch
 *  would forget. */
export function outcome(row: InterviewSummary): Outcome {
  const said = narrate(row);
  const raised = flags(row);
  // `urgent` is the flag's own answer, not the ending's: a call the gate
  // flagged and let finish still owes a clinician a decision, and this is what
  // puts the red treatment on its row.
  const urgent = said.urgent || isRed(row);
  // The safety row's own copy already says what stopped it; two sentences for
  // one flag is two sentences to disagree.
  if (!raised || row.outcome === 'safety') return { ...said, urgent };
  return { ...said, urgent, detail: `${said.detail} · ${raised}` };
}

/**
 * The Outcome column. `status` says how far it got and `outcome` says why it
 * stopped — the row needs both, because "abandoned / complete" is a call the
 * patient finished on a script the machine did not.
 */
function narrate(row: InterviewSummary): Outcome {
  const { capturedFields: got, totalFields: all } = row;
  const captured = all ? `${got} of ${all} declared items captured` : 'nothing captured yet';
  const ran = minutes(row);

  if (row.outcome === 'safety') {
    return {
      title: 'Urgent escalation · red flag',
      detail: 'Stopped by the gate before generation · a clinician was asked to make contact',
      urgent: true,
    };
  }
  if (row.status === 'queued') {
    return { title: 'Not started', detail: 'Invited, waiting on the patient', urgent: false };
  }
  if (row.status === 'running') {
    return { title: 'In progress', detail: `On the line now · ${captured}`, urgent: false };
  }
  if (row.status === 'completed') {
    return { title: 'Completed', detail: `Ran to the end · ${captured}`, urgent: false };
  }
  if (row.outcome === 'patient_left') {
    return {
      title: ran ? `Abandoned after ${ran} minutes` : 'Abandoned',
      detail: `The patient left mid-interview · ${captured}`,
      urgent: false,
    };
  }
  if (row.outcome === 'max_duration') {
    return { title: 'Cut short at the duration ceiling', detail: captured, urgent: false };
  }
  if (row.outcome === 'server_shutdown') {
    return {
      title: 'Interrupted by a deploy',
      detail: `The patient heard the goodbye · ${captured}`,
      urgent: false,
    };
  }
  if (row.outcome === 'complete' || row.outcome === 'ended_by_patient') {
    return { title: 'Ended early', detail: `The script did not finish · ${captured}`, urgent: false };
  }
  if (row.outcome === 'pipeline_finished') {
    // `lifecycle.py`'s fallback: the pipeline went quiet and neither the bot
    // nor the store had a reason. Say that, rather than showing a clinician an
    // identifier out of our own source.
    return { title: 'Ended without a recorded reason', detail: captured, urgent: false };
  }
  return { title: 'Issue raised', detail: `${row.outcome ?? 'no outcome recorded'} · ${captured}`, urgent: false };
}

export type PillKind = 'danger' | 'accent' | 'warn' | 'faint' | '';

export function statusPill(row: InterviewSummary): { label: string; kind: PillKind } {
  // Red first, and from the flag rather than the ending — `outcome === 'safety'`
  // stays because a stopped call is red even if its scan rows are unreadable,
  // but it is no longer the only way in. Yellow below is now what it says it
  // is: the gate found something that does not owe anybody a decision.
  if (row.outcome === 'safety' || isRed(row)) return { label: 'urgent review', kind: 'danger' };
  // A completed call the gate flagged is not the same errand as a completed
  // call it cleared, and the pill is what the eye reads first.
  if (row.status === 'completed' && row.flagCount > 0) return { label: 'flagged', kind: 'warn' };
  if (row.status === 'completed') return { label: 'review ready', kind: 'accent' };
  if (row.status === 'running') return { label: 'in progress', kind: '' };
  if (row.status === 'queued') return { label: 'not started', kind: 'faint' };
  return { label: 'issue raised', kind: 'warn' };
}

/* ── patient identity ──────────────────────────────────────────────────────
   Real values, formatted. An NHS number is seeded, from the range NHS England
   reserves for test data, and held under a CHECK in `clinical.patients` that
   will not accept anything else; a date of birth is a column. That is why they
   are here rather than in `demo.ts`. */

/** The spec's masked form — the last three digits only, which is what a
 *  clinician actually scans a column for. The full number reaches the browser
 *  because the patients search matches on it; the column does not show it. */
export function nhsMasked(nhsNumber: string | null): string {
  if (!nhsNumber) return 'NHS —';
  return `NHS ···· ${nhsNumber.slice(-3)}`;
}

const BORN = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

/** A date of birth as the header draws it, and the age it implies.
 *
 *  UTC throughout: the column is a `date` and arrives as `1951-03-14`, so
 *  parsing it in local time would move a birthday a day in either direction
 *  depending on where the clinician is sitting. */
export function dob(iso: string | null): { label: string; age: number | null } {
  if (!iso) return { label: '—', age: null };
  const born = new Date(`${iso}T00:00:00Z`);
  const now = new Date();
  const month = born.getUTCMonth();
  const day = born.getUTCDate();
  let age = now.getUTCFullYear() - born.getUTCFullYear();
  if (now.getUTCMonth() < month || (now.getUTCMonth() === month && now.getUTCDate() < day)) {
    age -= 1;
  }
  return { label: BORN.format(born), age };
}
