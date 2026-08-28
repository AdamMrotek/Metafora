import { useMemo, useState } from 'react';
import type { InterviewSummary } from '@metafora/contracts';
import { useRecord } from '../data.tsx';
import * as demo from '../demo.ts';
import { activityAt, greeting, outcome, relative, stamp, statusPill, today } from '../format.ts';
import { Link, navigate } from '../router.tsx';
import { Experience } from './Experience.tsx';

/**
 * The dashboard answers three questions, in this order, and the order is the
 * layout:
 *
 *   Is anything on fire?      → the band above this screen
 *   What do I owe a decision on? → the tiles and the review table
 *   Who is on my list?           → the calls still out, and the Patients screen
 */

const PAGE = 8;

export function Dashboard() {
  const { interviews, loading, error } = useRecord();
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<'urgency' | 'recent'>('urgency');
  const [protocol, setProtocol] = useState('all');
  const [page, setPage] = useState(0);

  const protocols = useMemo(
    () => [...new Set(interviews.map((i) => i.protocolLabel))].sort(),
    [interviews],
  );

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = interviews.filter(
      (row) =>
        (protocol === 'all' || row.protocolLabel === protocol) &&
        (!needle ||
          row.patientFirstName.toLowerCase().includes(needle) ||
          row.protocolLabel.toLowerCase().includes(needle) ||
          row.id.toLowerCase().includes(needle)),
    );
    // Urgency first by default, because the table exists to be triaged rather
    // than browsed: an escalation from yesterday outranks a completed call
    // from ten minutes ago.
    return filtered.sort((a, b) => {
      if (sort === 'urgency') {
        const rank = (r: InterviewSummary) => (r.outcome === 'safety' ? 0 : r.status === 'abandoned' ? 1 : 2);
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
      }
      return activityAt(b).localeCompare(activityAt(a));
    });
  }, [interviews, query, sort, protocol]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = rows.slice(current * PAGE, current * PAGE + PAGE);

  const ready = interviews.filter((i) => i.status === 'completed').length;
  const running = interviews.filter((i) => i.status === 'running').length;
  const queued = interviews.filter((i) => i.status === 'queued').length;
  const urgent = interviews.filter((i) => i.outcome === 'safety').length;

  return (
    <div className="page">
      <div className="greet">
        <b>
          {greeting()} <i>· {new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</i>
        </b>
        <span>
          {today()} · {loading ? 'loading the record…' : `${interviews.length} interviews on your list`}
        </span>
      </div>

      {/* The escalation count is deliberately absent: the band above already
          carries it, with a clock. Two places for one number is two places to
          disagree. */}
      <div className="stats">
        <span className="stat">
          <span className="stat__n">{ready}</span>
          <span className="stat__l">Review ready</span>
        </span>
        <span className="stat">
          <span className="stat__n">
            {running}
            {running > 0 && <em>on the line</em>}
          </span>
          <span className="stat__l">In progress</span>
        </span>
        {/* The spec's third tile is "Expiring · 48h", which counts invitation
            windows. `clinical.invitations` is Phase 5, so the slot carries a
            number that exists instead of one that does not. */}
        <span className="stat stat--muted">
          <span className="stat__n">{queued}</span>
          <span className="stat__l">Queued</span>
        </span>
      </div>

      <div className="panel">
        <div className="toolbar">
          <span className="toolbar__t">Review queue</span>
          <span className="toolbar__c">
            {rows.length} shown · {urgent} urgent
          </span>
          <span className="grow" />
          <input
            className="ctl ctl--search"
            placeholder="Search patient or protocol"
            aria-label="Search patient or protocol"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
          />
          <select
            className="ctl"
            aria-label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as typeof sort)}
          >
            <option value="urgency">Sort urgency</option>
            <option value="recent">Sort most recent</option>
          </select>
          <select
            className="ctl"
            aria-label="Protocol"
            value={protocol}
            onChange={(e) => {
              setProtocol(e.target.value);
              setPage(0);
            }}
          >
            <option value="all">Protocol all</option>
            {protocols.map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </select>
        </div>

        {error ? (
          <p className="note note--bad">
            <b>The record could not be read</b>
            {error}
          </p>
        ) : loading ? (
          <p className="note">Reading the record…</p>
        ) : shown.length === 0 ? (
          <p className="note">
            <b>Nothing here yet</b>
            {interviews.length === 0
              ? 'No interview has been recorded against your caseload. Complete a call on the patient portal and it lands here.'
              : 'No interview matches that search.'}
          </p>
        ) : (
          <>
            <table className="dt">
              <colgroup>
                <col style={{ width: '15%' }} />
                <col style={{ width: '19%' }} />
                <col style={{ width: '17%' }} />
                <col style={{ width: '27%' }} />
                <col style={{ width: '22%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Interview</th>
                  <th className={sort === 'recent' ? 'sorted' : undefined}>Ended ↓</th>
                  <th>Outcome</th>
                  <th className={sort === 'urgency' ? 'sorted' : undefined}>Status</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <Row key={row.id} row={row} />
                ))}
              </tbody>
            </table>

            <div className="pager">
              <span>
                showing {current * PAGE + 1}–{Math.min(rows.length, current * PAGE + PAGE)} of{' '}
                {rows.length}
              </span>
              <span className="pages">
                <span
                  className={current === 0 ? 'pg pg--off' : 'pg'}
                  data-page={current === 0 ? undefined : 'prev'}
                  onClick={() => setPage(Math.max(0, current - 1))}
                >
                  ‹
                </span>
                {Array.from({ length: pages }, (_, i) => (
                  <span
                    key={i}
                    className={i === current ? 'pg pg--on' : 'pg'}
                    data-page={i}
                    onClick={() => setPage(i)}
                  >
                    {i + 1}
                  </span>
                ))}
                <span
                  className={current >= pages - 1 ? 'pg pg--off' : 'pg'}
                  data-page={current >= pages - 1 ? undefined : 'next'}
                  onClick={() => setPage(Math.min(pages - 1, current + 1))}
                >
                  ›
                </span>
              </span>
            </div>
          </>
        )}
      </div>

      <div className="duo">
        <Scheduled />
        <Experience />
      </div>
    </div>
  );
}

function Row({ row }: { row: InterviewSummary }) {
  const said = outcome(row);
  const pill = statusPill(row);
  const href = `/interviews/${row.id}`;
  const percent = row.totalFields ? (row.capturedFields / row.totalFields) * 100 : 0;

  return (
    <tr
      className={said.urgent ? 'tr--urgent' : undefined}
      data-href={href}
      onClick={() => navigate(href)}
    >
      <td>
        <span className="who">{row.patientFirstName}</span>
        <span className="sub mono">{demo.nhsNumber(row.patientId)}</span>
      </td>
      <td>
        {row.protocolLabel} <span className="ver">{row.protocolId.split('_').pop()}</span>
        <span className="meter">
          <span className="meter__t">
            <span
              className={percent < 70 ? 'meter__f meter__f--low' : 'meter__f'}
              style={{ width: `${percent}%` }}
            />
          </span>
          {row.capturedFields}/{row.totalFields} captured
        </span>
      </td>
      <td className="mono">
        {row.endedAt ? stamp(row.endedAt) : '–'}
        <span className="sub">{row.endedAt ? relative(row.endedAt) : 'not ended'}</span>
      </td>
      <td className="out">
        <b>{said.title}</b>
        <span>{said.detail}</span>
      </td>
      <td>
        <span className="status">
          <span className={pill.kind ? `pill pill--${pill.kind}` : 'pill'}>
            {pill.kind === 'danger' && <span className="pill__d" />}
            {pill.label}
          </span>
          <Link className="act-link" to={href} aria-label={`Open ${row.patientFirstName}'s interview`}>
            →
          </Link>
        </span>
      </td>
    </tr>
  );
}

/**
 * What is still out. Real, and empty until Phase 5 dispatches anything —
 * `scheduled_for` is only written by a clinician queueing a call, and nothing
 * queues one yet. The empty state says that rather than showing a blank card.
 */
function Scheduled() {
  const { interviews } = useRecord();
  const upcoming = interviews
    .filter((row) => row.status === 'queued')
    .sort((a, b) => (a.scheduledFor ?? a.createdAt).localeCompare(b.scheduledFor ?? b.createdAt))
    .slice(0, 4);

  return (
    <div className="card">
      <div className="card__h">
        <span>Scheduled care calls</span>
        <span>{upcoming.length} waiting</span>
      </div>
      {upcoming.length === 0 ? (
        <p className="note">
          <b>Nothing is out</b>
          Queueing a call to a patient is dispatch, which arrives with the Deployments screen.
        </p>
      ) : (
        <div className="card__grid">
          {upcoming.map((row) => (
            <div className="item" key={row.id}>
              <span className="item__t">
                <span className="item__n">{row.patientFirstName}</span>
                <span className="item__m">
                  {row.scheduledFor ? relative(row.scheduledFor) : 'unscheduled'}
                </span>
              </span>
              <span className="item__s">
                {row.protocolLabel} · invited, not started
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="card__f">Manage in Deployments →</div>
    </div>
  );
}
