/**
 * Everything on this screen that no query produced.
 *
 * `docs/ux/clinical-dashboard.html` draws NHS numbers, dates of birth, consent
 * chips, a signature ledger and a patient-experience chart. None of those exist
 * in the backend: the ledger and the escalations table are Phase 5, and NHS
 * number and DOB are demographics this product has never collected — a demo
 * visitor gives a first name and nothing else.
 *
 * They are drawn anyway, because the phase is building the spec's screens. So
 * every invented value is in this one file, and nowhere else, for three
 * reasons: you can read what is illustrative in one sitting, no component can
 * quietly start inventing its own, and Phase 5 deletes a file rather than
 * hunting through six.
 *
 * Everything here is derived from a real id, so a patient's number does not
 * change when the page reloads — a screen whose "record" moves between renders
 * is worse than one that shows nothing.
 *
 * **Nothing in here is clinical, and nothing in here should ever be believed.**
 * The chrome carries a `demo data` chip for exactly that reason.
 */

/** FNV-1a. A stable, boring hash — the point is determinism, not distribution. */
function seed(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

function pick<T>(values: readonly T[], key: string, salt = ''): T {
  return values[seed(key + salt) % values.length] as T;
}

/** The spec's masked form — the last three digits only, which is what a
 *  clinician actually scans a column for. */
export function nhsNumber(patientId: string): string {
  return `NHS ···· ${String(seed(patientId) % 1000).padStart(3, '0')}`;
}

export function dateOfBirth(patientId: string): { label: string; age: number } {
  const n = seed(`dob:${patientId}`);
  const year = 1948 + (n % 52);
  const month = n % 12;
  // `>>>`, not `>>`: the seed is a full uint32, and a signed shift on anything
  // above 2^31 comes back negative — which reaches the screen as a date in
  // 1947 or, on the chart below, a count of minus one.
  const day = 1 + ((n >>> 4) % 27);
  const born = new Date(Date.UTC(year, month, day));
  const now = new Date();
  let age = now.getUTCFullYear() - year;
  if (now.getUTCMonth() < month || (now.getUTCMonth() === month && now.getUTCDate() < day)) age -= 1;
  return {
    label: born.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }),
    age,
  };
}

/** The interview detail's consent chip. Always granted, because a call that
 *  ran is a call somebody consented to — the chip records it, it does not
 *  decide it. */
export const consent = 'consent · processing, recording';

/** The composer's two hashes. There is no ledger — Phase 5 builds
 *  `clinical.signatures` and its `(prev_hash, record_hash, hash)` chain — so
 *  these are shaped like the thing that will replace them and are nothing else. */
export function hashes(interviewId: string): { record: string; head: string } {
  const hex = (key: string) => seed(key).toString(16).padStart(8, '0');
  return {
    record: `${hex(interviewId).slice(0, 4)}…${hex(`r:${interviewId}`).slice(0, 4)}`,
    head: `${hex('ledger-head').slice(0, 4)}…${hex('ledger-head-2').slice(0, 4)}`,
  };
}

/** A surgical context for the breadcrumb and the issued summary. The protocol
 *  is real; what it is *for* is not recorded anywhere. */
export const referral = (patientId: string): string =>
  pick(
    [
      'elective cholecystectomy',
      'elective hip arthroplasty',
      'day-case hernia repair',
      'elective cataract surgery',
    ],
    patientId,
    'referral',
  );

/* ── patient experience ────────────────────────────────────────────────────
   The one panel with nothing behind it at all. Nothing asks a patient how the
   interview went, and nothing stores the answer — the opt-in survey is not on
   any roadmap phase. It is drawn because the spec's caption promises a chart
   that moves when you change the range, and a static picture of one would be
   a worse lie than a labelled placeholder. */

export type Range = 'today' | 'week' | 'all';
export type Day = { label: string; positive: number; neutral: number; negative: number };

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function experience(range: Range): { days: Day[]; scope: string } {
  const size = range === 'today' ? 1 : range === 'week' ? 7 : 14;
  const days: Day[] = [];
  for (let i = size - 1; i >= 0; i -= 1) {
    const date = new Date();
    date.setDate(date.getDate() - i);
    const n = seed(`px:${range}:${date.toDateString()}`);
    days.push({
      label: size > 7 ? `${date.getDate()}` : (DAY_LABELS[date.getDay()] ?? ''),
      positive: 4 + (n % 9),
      neutral: 1 + ((n >>> 3) % 4),
      negative: (n >>> 6) % 3,
    });
  }
  return {
    days,
    scope:
      range === 'today'
        ? 'responses today'
        : range === 'week'
          ? 'responses per day · last 7 days'
          : 'responses per day · last 14 days',
  };
}
