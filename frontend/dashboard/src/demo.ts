/**
 * Everything on this screen that no query produced.
 *
 * `docs/ux/clinical-dashboard.html` draws more than the backend holds. Phase
 * 5·0 closed most of the gap — NHS numbers and dates of birth are seeded rows
 * under a test-range CHECK and come down with the summaries, and the
 * patient-experience chart is a real scoped read over `metrics` — so what is
 * left here is three things and a shrinking list:
 *
 * - `referral`, because the protocol is recorded and what it is *for* is not;
 * - `consent`, which stays invented deliberately: `docs/system-map.md` says
 *   claiming a consent record you do not hold is worse than holding none, and
 *   seeding one would be exactly that. It goes when a consent record exists or
 *   the chip goes;
 * - `hashes`, which is 5c's to delete when `clinical.signatures` lands.
 *
 * They are drawn anyway, because the phase is building the spec's screens. So
 * every invented value is in this one file, and nowhere else, for three
 * reasons: you can read what is illustrative in one sitting, no component can
 * quietly start inventing its own, and the last phase deletes a file rather
 * than hunting through six.
 *
 * Everything here is derived from a real id, so a patient's referral does not
 * change when the page reloads — a screen whose "record" moves between renders
 * is worse than one that shows nothing.
 *
 * **Nothing in here is clinical, and nothing in here should ever be believed.**
 * The chrome carries a `demo data` chip for exactly that reason. Formatting of
 * values that *are* real lives in `format.ts`.
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

/** The interview detail's consent chip. Always granted, because a call that
 *  ran is a call somebody consented to — the chip records it, it does not
 *  decide it. */
export const consent = 'consent · processing, recording';

/** The composer's two hashes. There is no ledger — Phase 5c builds
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
