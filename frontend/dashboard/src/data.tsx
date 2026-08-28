import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { InterviewSummary, PatientSummary } from '@metafora/contracts';
import { get } from './api.ts';
import { activityAt } from './format.ts';

/**
 * The two list reads, fetched once and shared.
 *
 * Four things want the interview list — the escalation rail, the review table,
 * the scheduled-calls card and the detail screen's history timeline — and they
 * are on screen at the same time. Fetching per component would be four requests
 * for one answer, and four chances for the rail to disagree with the table.
 *
 * Reads only, as `reads.py` is reads only, so there is no cache to invalidate:
 * `reload` exists for the one case that matters, which is a call ending while
 * the dashboard is open.
 */

type Record = {
  interviews: InterviewSummary[];
  patients: PatientSummary[];
  loading: boolean;
  error: string | null;
  reload: () => void;
};

const RecordContext = createContext<Record | null>(null);

export function RecordProvider({ children }: { children: ReactNode }) {
  const [interviews, setInterviews] = useState<InterviewSummary[]>([]);
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([get<InterviewSummary[]>('/interviews'), get<PatientSummary[]>('/patients')])
      .then(([iv, pt]) => {
        if (!live) return;
        setInterviews(iv);
        setPatients(pt);
        setError(null);
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [nonce]);

  return (
    <RecordContext.Provider
      value={{ interviews, patients, loading, error, reload: () => setNonce((n) => n + 1) }}
    >
      {children}
    </RecordContext.Provider>
  );
}

export function useRecord(): Record {
  const value = useContext(RecordContext);
  if (!value) throw new Error('useRecord outside RecordProvider');
  return value;
}

/** Every interview for one patient, oldest first — the detail screen's
 *  timeline, and the patients table's "last interview" column. */
export function historyOf(interviews: InterviewSummary[], patientId: string): InterviewSummary[] {
  return interviews
    .filter((i) => i.patientId === patientId)
    .sort((a, b) => activityAt(a).localeCompare(activityAt(b)));
}

/** Still owed a decision: an escalation nobody has cleared, or a call that did
 *  not finish. Phase 5's signature ledger is what will make this exact; until
 *  then it is the same predicate the review table's warn pills use. */
export const isOpen = (row: InterviewSummary): boolean =>
  row.outcome === 'safety' || row.status === 'abandoned';
