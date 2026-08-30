import { useEffect, useState } from 'react';
import type { InterviewSummary } from '@metafora/contracts';
import { CopyLink } from '../CopyLink.tsx';
import { EMPTY_OVERVIEW, useInterviews, useRecord } from '../data.tsx';
import {
  greeting,
  nhsMasked,
  outcome,
  relative,
  stamp,
  statusPill,
  today,
} from '../format.ts';
import { Link, navigate } from '../router.tsx';
import { Experience } from './Experience.tsx';

/**
 * The dashboard answers three questions, in this order, and the order is the
 * layout:
 *
 *   Is anything on fire?      → the band above this screen
 *   What do I owe a decision on? → the tiles and the review table
 *   Who is on my list?           → the calls still out, and the Patients screen
 *
 * The three are separate reads because they are separate questions. In
 * particular the review table holds calls that *happened*: a queued interview
 * has nothing to review, and it is drawn by the scheduled card below and by the
 * Deployments screen, which is where a call still out belongs. The spec calls
 * this panel "Review ready" for that reason.
 */

//: Rows per page. Server-side now, so this is the size of the request rather
//: than the size of a slice — and a number the pager under the table can be
//: honest about, because `total` comes back with the rows.
const PAGE = 12;

export function Dashboard() {
  const { overview, loading: loadingOverview, error: overviewError } = useRecord();
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<'urgency' | 'recent'>('urgency');
  const [protocol, setProtocol] = useState('all');
  const [page, setPage] = useState(0);

  const { page: table, loading, error } = useInterviews({
    sort,
    search,
    protocol,
    page,
    limit: PAGE,
  });

  const tiles = overview ?? EMPTY_OVERVIEW;
  const rows = table?.rows ?? [];
  const total = table?.total ?? 0;
  const pages = table?.pages ?? 1;

  // A filter that narrows the result can leave the reader past the last page.
  // The server answers that honestly — no rows, and the real total — and this
  // walks them back rather than leaving them on an empty table.
  useEffect(() => {
    if (table && page > 0 && page >= table.pages) setPage(table.pages - 1);
  }, [table, page]);

  return (
    <div className="page">
      <div className="greet">
        <b>
          {greeting()} <i>· {new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</i>
        </b>
        <span>
          {today()} ·{' '}
          {loadingOverview
            ? 'loading the record…'
            : `${total} ${total === 1 ? 'call' : 'calls'} to review · ${tiles.queued.length} still out`}
        </span>
      </div>

      {/* The band above this screen carries the escalation too, with a clock,
          and it is the thing that interrupts. This is the second reading: a
          summary that runs "flagged · incomplete" and silently omits the most
          serious category is a summary that reassures.

          All three are counted by `reads.overview` over the whole caseload.
          They used to be counted in the browser out of the same fetch the table
          used — which was only ever right while that fetch *was* the record,
          and became a count of one page the moment the table paged properly.

          The spec's third tile is "Expiring · 48h", which counts invitation
          windows. `clinical.invitations` exists since Phase 5a, but a link
          nearing its expiry is not work owed — the other two are — so the slot
          keeps the number a clinician has to act on. */}
      <div className="stats">
        <span className="stat stat--urgent">
          <span className="stat__n">{tiles.urgent}</span>
          <span className="stat__l">Urgent</span>
        </span>
        <span className="stat stat--flag">
          <span className="stat__n">{tiles.flagged}</span>
          <span className="stat__l">Flagged</span>
        </span>
        <span className="stat">
          <span className="stat__n">{tiles.incomplete}</span>
          <span className="stat__l">Incomplete</span>
        </span>
      </div>

      <div className="panel">
        <div className="toolbar">
          <span className="toolbar__t">Review queue</span>
          <span className="toolbar__c">
            {total} matching · {tiles.urgent} urgent
          </span>
          <span className="grow" />
          <input
            className="ctl ctl--search"
            placeholder="Search patient or protocol"
            aria-label="Search patient or protocol"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
          />
          <select
            className="ctl"
            aria-label="Sort"
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as typeof sort);
              setPage(0);
            }}
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
            {/* What the record actually holds, from `overview` — never every
                protocol that exists, because an option that selects nothing is
                a filter lying about the record. */}
            {tiles.protocols.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        {error || overviewError ? (
          <p className="note note--bad">
            <b>The record could not be read</b>
            {error ?? overviewError}
          </p>
        ) : loading && !table ? (
          <p className="note">Reading the record…</p>
        ) : rows.length === 0 ? (
          <p className="note">
            <b>Nothing here yet</b>
            {search.trim() || protocol !== 'all'
              ? 'No interview matches that search.'
              : 'No call has been taken against your caseload yet. Calls still out are on the Deployments screen; one lands here the moment a patient starts it.'}
          </p>
        ) : (
          <>
            <table className="dt" aria-busy={loading}>
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
                {rows.map((row) => (
                  <Row key={row.id} row={row} />
                ))}
              </tbody>
            </table>

            <div className="pager">
              <span>
                showing {page * PAGE + 1}–{Math.min(total, page * PAGE + rows.length)} of {total}
              </span>
              <span className="pages">
                <span
                  className={page === 0 ? 'pg pg--off' : 'pg'}
                  data-page={page === 0 ? undefined : 'prev'}
                  onClick={() => setPage(Math.max(0, page - 1))}
                >
                  ‹
                </span>
                {Array.from({ length: pages }, (_, i) => (
                  <span
                    key={i}
                    className={i === page ? 'pg pg--on' : 'pg'}
                    data-page={i}
                    onClick={() => setPage(i)}
                  >
                    {i + 1}
                  </span>
                ))}
                <span
                  className={page >= pages - 1 ? 'pg pg--off' : 'pg'}
                  data-page={page >= pages - 1 ? undefined : 'next'}
                  onClick={() => setPage(Math.min(pages - 1, page + 1))}
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
        <span className="sub mono">{nhsMasked(row.patientNhsNumber)}</span>
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
 * What is still out: every call queued and not yet started.
 *
 * The copy button lives here and nowhere else in the review path, because these
 * are the only rows for which a link means anything — it is spent the moment
 * the call begins, so a completed interview has nothing to copy.
 */
function Scheduled() {
  const { overview } = useRecord();
  const upcoming = (overview?.queued ?? []).slice(0, 4);
  const waiting = overview?.queued.length ?? 0;

  return (
    <div className="card">
      <div className="card__h">
        <span>Scheduled care calls</span>
        <span>{waiting} waiting</span>
      </div>
      {upcoming.length === 0 ? (
        <p className="note">
          <b>Nothing is out</b>
          Queue a call to a patient on the Deployments screen and it waits here until they take
          it.
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
                <CopyLink interviewId={row.id} label={`Copy ${row.patientFirstName}’s link`} />
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="card__f">
        <Link to="/deployments">Manage in Deployments →</Link>
      </div>
    </div>
  );
}
