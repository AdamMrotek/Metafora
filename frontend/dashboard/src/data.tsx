import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { InterviewPage, Overview, PatientSummary } from '@metafora/contracts';
import { get } from './api.ts';

/**
 * What the whole screen shares, and what one table asks for itself.
 *
 * These used to be one thing: a single `GET /interviews` returning the hundred
 * most recent rows, which five components then filtered, counted and paged in
 * the browser. That was workable while a hundred rows *was* the record. It
 * stopped being workable the moment the table paged properly, because a tile
 * counting the eight rows on screen is a tile counting a page and calling it a
 * caseload — and it was already wrong before that, at a hundred and one
 * interviews, silently.
 *
 * So the split is by what a number is *about*:
 *
 * - `overview` — the three tiles, the escalation band, what is still out, and
 *   the protocol filter's options. All about the whole caseload, none of them
 *   changing when someone sorts a table or types in its search box, so they are
 *   fetched once and shared.
 * - `patients` — the same, for the patients screen, which now carries what its
 *   rows draw rather than deriving it from an interview list.
 * - the review table's page — `useInterviews` below, which is not shared,
 *   because it is the one thing that *does* change on every keystroke.
 *
 * `reload` exists for the case that matters: a call ending, or one being
 * queued, while the dashboard is open.
 */

type Record = {
  overview: Overview | null;
  patients: PatientSummary[];
  loading: boolean;
  error: string | null;
  reload: () => void;
};

const RecordContext = createContext<Record | null>(null);

/** An overview before one has arrived, so components can render their frame
 *  without a null check on every field. Zeroes, never placeholders. */
export const EMPTY_OVERVIEW: Overview = {
  urgent: 0,
  flagged: 0,
  incomplete: 0,
  escalations: [],
  queued: [],
  protocols: [],
};

export function RecordProvider({ children }: { children: ReactNode }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([get<Overview>('/overview'), get<PatientSummary[]>('/patients')])
      .then(([ov, pt]) => {
        if (!live) return;
        setOverview(ov);
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
      value={{ overview, patients, loading, error, reload: () => setNonce((n) => n + 1) }}
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

export type TableQuery = {
  sort: 'urgency' | 'recent';
  search: string;
  protocol: string;
  page: number;
  limit: number;
};

/**
 * One page of the review table.
 *
 * Every field of `TableQuery` becomes a query parameter, because every one of
 * them is now a decision the database makes over the whole record rather than a
 * decision the browser makes about rows it was already given.
 *
 * The search is debounced. Sorting and paging are one deliberate click each and
 * go straight out; typing is not, and a request per keystroke would be a query
 * per character of a patient's name.
 */
export function useInterviews(query: TableQuery) {
  const [page, setPage] = useState<InterviewPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const search = useDebounced(query.search, 250);

  const path = useMemo(() => {
    const params = new URLSearchParams({
      sort: query.sort,
      page: String(query.page),
      limit: String(query.limit),
    });
    if (search.trim()) params.set('q', search.trim());
    if (query.protocol !== 'all') params.set('protocol', query.protocol);
    return `/interviews?${params}`;
  }, [query.sort, query.page, query.limit, query.protocol, search]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    get<InterviewPage>(path)
      .then((value) => {
        if (!live) return;
        setPage(value);
        setError(null);
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [path, nonce]);

  return { page, loading, error, reload: () => setNonce((n) => n + 1) };
}

/** Hold a value still until it has stopped changing for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [held, setHeld] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setHeld(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return held;
}
