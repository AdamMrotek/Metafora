import type { InterviewSummary } from '@metafora/contracts';

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

/** The gap between two contacts, as the timeline's spine labels it. */
export function gap(fromIso: string, toIso: string): string {
  const days = Math.round(
    (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 86_400_000,
  );
  if (days < 14) return `${Math.max(days, 0)}d`;
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

/**
 * The Outcome column. `status` says how far it got and `outcome` says why it
 * stopped — the row needs both, because "abandoned / complete" is a call the
 * patient finished on a script the machine did not.
 */
export function outcome(row: InterviewSummary): Outcome {
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
  if (row.outcome === 'safety') return { label: 'urgent review', kind: 'danger' };
  if (row.status === 'completed') return { label: 'review ready', kind: 'accent' };
  if (row.status === 'running') return { label: 'in progress', kind: '' };
  if (row.status === 'queued') return { label: 'not started', kind: 'faint' };
  return { label: 'issue raised', kind: 'warn' };
}

/** Newest activity first — when the call happened, not when it was queued, so
 *  a backlog dispatched last week does not bury this morning's interview. It is
 *  the order `reads.interviews` already returns; repeated here because the
 *  screens re-sort and filter client-side. */
export const activityAt = (row: InterviewSummary): string =>
  row.endedAt ?? row.startedAt ?? row.scheduledFor ?? row.createdAt;
