import { useEffect, useState } from 'react';
import type { Account, Escalation } from '@metafora/contracts';
import { post } from './api.ts';
import { RecordProvider, useRecord } from './data.tsx';
import { elapsed } from './format.ts';
import { Link, useRoute } from './router.tsx';
import { Dashboard } from './screens/Dashboard.tsx';
import { Deployments } from './screens/Deployments.tsx';
import { Interview } from './screens/Interview.tsx';
import { Patients } from './screens/Patients.tsx';

/**
 * The clinician portal.
 *
 * Four screens onto one record, and a band that follows you across all of
 * them. The order the dashboard asks its questions in is the layout, and the
 * band is above all of it because "is anything on fire?" outranks every other
 * question on the screen.
 */
export function App({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  return (
    <RecordProvider>
      <div className="app">
        <Chrome account={account} onSignOut={onSignOut} />
        <Rail />
        <Screen />
      </div>
    </RecordProvider>
  );
}

function Screen() {
  const route = useRoute();
  if (route.name === 'interview') return <Interview id={route.id} />;
  if (route.name === 'patients') return <Patients />;
  if (route.name === 'deployments') return <Deployments />;
  return <Dashboard />;
}

function Chrome({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  const route = useRoute();
  const on = (name: string) => (route.name === name ? { 'aria-current': 'page' as const } : {});

  return (
    <div className="chrome">
      <span className="wordmark">
        metafora<span className="gradtext">.care</span>
      </span>
      <nav>
        <Link to="/" {...on('dashboard')}>
          Dashboard
        </Link>
        <Link to="/patients" {...on('patients')}>
          Patients
        </Link>
        <Link to="/deployments" {...on('deployments')}>
          Deployments
        </Link>
      </nav>
      <span className="grow" />
      <span
        className="demo-chip"
        title="Synthetic patients, on NHS test numbers. Three things are still drawn rather than recorded: the referral reason, the consent chip and the ledger hashes."
      >
        demo data
      </span>
      <span className="chrome__me">
        <b>{account.displayName}</b>
      </span>
      <button className="chrome__out" type="button" onClick={onSignOut}>
        Sign out
      </button>
      <span className="kbd">⌘K</span>
    </div>
  );
}

/**
 * The escalation band.
 *
 * Full bleed, red, above everything. It is not a notification: the count and
 * the clock come from the record — from `reads.overview`, over the whole
 * caseload, so the number is the number and not however many happened to be on
 * the page below — and only a human clears it.
 *
 * What it says is *what is owed*, not how bad it is. The two reds ask different
 * things — `end_call` stopped the call, so somebody has to make contact;
 * `urgent_escalate` let it run on, so a decision is owed by a time the
 * protocol's own timeout fixed — and they are told apart from `action`, which
 * the read already returns.
 *
 * It used to quote the patient. That meant a second request per band, fetching
 * a whole transcript to reconstruct the words on the turn the gate scanned;
 * the flag's own label says what those words were trying to say, and says it
 * from the protocol version the interview pinned.
 */
function Rail() {
  const { overview, reload } = useRecord();
  // Newest first, and counted over the whole caseload — `reads.overview` does
  // both, from one predicate, which is why the sentence and the count agree.
  const open = overview?.escalations ?? [];
  const total = overview?.urgent ?? 0;

  // Which of the open reds is on the band. The list is newest first, so 0 is
  // the one that just arrived and the arrows walk back through the rest in
  // place — a full-bleed red band that grew into a list would stop being a
  // band. Acknowledging shortens the list underneath us, so the position is
  // clamped on read and corrected in an effect rather than trusted.
  const [at, setAt] = useState(0);
  const index = open.length === 0 ? 0 : Math.min(at, open.length - 1);
  const latest = open[index];
  const step = (by: number) => setAt((n) => (n + by + open.length) % open.length);
  useEffect(() => {
    if (at !== index) setAt(index);
  }, [at, index]);

  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  void tick;

  const [taking, setTaking] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);

  // Clear the last refusal when the band moves on to a different call, so a
  // sentence about one interview cannot sit under another one's name.
  useEffect(() => setRefused(null), [latest?.interviewId]);

  if (!latest) return null;

  async function take(id: string) {
    setTaking(true);
    setRefused(null);
    try {
      await post(`/interviews/${id}/acknowledge`);
      // The band, the `urgent` tile and the review table's row all move on the
      // same fact, so the whole shared record is refetched rather than this one
      // line being spliced out of it.
      reload();
    } catch (error) {
      setRefused((error as Error).message);
    } finally {
      setTaking(false);
    }
  }

  return (
    <div className="rail">
      <span className="rail__dot" />
      <span className="rail__txt">
        <span className="rail__t">
          {total === 1
            ? '1 red flag is waiting for a clinician'
            : `${total} red flags are waiting for a clinician`}
        </span>
        <span className="rail__s">
          {refused ?? `${latest.patientFirstName} · ${latest.flagLabel} · ${owed(latest)}`}
        </span>
      </span>
      {open.length > 1 && (
        <span className="rail__nav">
          <button
            className="rail__arrow"
            type="button"
            aria-label="Previous red flag"
            onClick={() => step(-1)}
          >
            ‹
          </button>
          {/* The position, not the count — the headline above already says how
              many are waiting, and `total` counts the whole caseload while this
              list is capped. */}
          <span className="rail__i" aria-live="polite">
            {index + 1} of {open.length}
          </span>
          <button
            className="rail__arrow"
            type="button"
            aria-label="Next red flag"
            onClick={() => step(1)}
          >
            ›
          </button>
        </span>
      )}
      <span className="rail__c">{elapsed(latest.raisedAt)}</span>
      {/* Read first, take second. Acknowledging is a statement that somebody
          has this call, and the primary action on the band is the one that
          lets them make it truthfully. */}
      <Link className="rail__b" to={`/interviews/${latest.interviewId}`}>
        Read it
      </Link>
      <button
        className="rail__b rail__b--ghost"
        type="button"
        disabled={taking}
        onClick={() => take(latest.interviewId)}
      >
        {taking ? 'Acknowledging…' : 'Acknowledge'}
      </button>
    </div>
  );
}

/** What this flag asks of a human, in the band's own words. */
function owed(escalation: Escalation): string {
  if (escalation.action === 'end_call') return 'call stopped — make contact';
  if (!escalation.dueAt) return 'decision owed';
  const by = new Date(escalation.dueAt).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  });
  return `decision owed by ${by}`;
}
