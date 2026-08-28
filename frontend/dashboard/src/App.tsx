import { useEffect, useState } from 'react';
import type { Account, InterviewDetail, InterviewSummary } from '@metafora/contracts';
import { get } from './api.ts';
import { RecordProvider, useRecord } from './data.tsx';
import { elapsed } from './format.ts';
import { Link, useRoute } from './router.tsx';
import { Dashboard } from './screens/Dashboard.tsx';
import { Interview } from './screens/Interview.tsx';
import { Patients } from './screens/Patients.tsx';
import { flagged } from './transcript.ts';

/**
 * The clinician portal.
 *
 * Three screens onto one record, and a band that follows you across all of
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
        {/* Dispatch is Phase 5. Drawn because the spec's chrome has three
            words in it, inert because a nav item that goes nowhere is better
            than one that goes somewhere unfinished. */}
        <a aria-disabled="true" title="Dispatch arrives in Phase 5">
          Deployments
        </a>
      </nav>
      <span className="grow" />
      <span className="demo-chip" title="Synthetic data. Some of what is drawn is illustrative until dispatch and the signature ledger are built.">
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
 * the clock come from the record, and nothing here clears it — only a human
 * does, in Phase 5, when `clinical.escalations` exists and Schedule means
 * something. Until then the button is inert and says so, because a button that
 * cannot honour what it offers is worse than no button.
 */
function Rail() {
  const { interviews } = useRecord();
  const escalations = interviews
    .filter((row) => row.outcome === 'safety')
    .sort((a, b) => (b.endedAt ?? '').localeCompare(a.endedAt ?? ''));
  const latest = escalations[0];

  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  void tick;

  const quote = useFlaggedWords(latest);

  if (!latest) return null;
  const owed = latest.endedAt ?? latest.createdAt;

  return (
    <div className="rail">
      <span className="rail__dot" />
      <span className="rail__txt">
        <span className="rail__t">
          {escalations.length === 1
            ? '1 escalation needs a consultation scheduled'
            : `${escalations.length} escalations need a consultation scheduled`}
        </span>
        <span className="rail__s">
          {latest.patientFirstName}
          {quote ? ` · “${quote}”` : ' · red flag raised before generation'} · raised{' '}
          {new Date(owed).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
        </span>
      </span>
      <span className="rail__c">{elapsed(owed)}</span>
      <Link className="rail__b" to={`/interviews/${latest.id}`}>
        Read it
      </Link>
    </div>
  );
}

/**
 * The patient's own words, from the turn the gate stopped on.
 *
 * A second request, made lazily for one interview, because the list route does
 * not carry transcripts and should not: the band quotes one person and the
 * table has a hundred rows.
 */
function useFlaggedWords(row: InterviewSummary | undefined): string | null {
  const [quote, setQuote] = useState<string | null>(null);

  useEffect(() => {
    if (!row) return;
    let live = true;
    setQuote(null);
    get<InterviewDetail>(`/interviews/${row.id}`)
      .then((detail) => live && setQuote(flagged(detail.events)))
      .catch(() => {
        /* the band still stands up without the quote */
      });
    return () => {
      live = false;
    };
  }, [row?.id]);

  return quote;
}
