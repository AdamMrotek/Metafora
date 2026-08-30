import { useEffect, useState } from 'react';
import type { InterviewDetail, InterviewSummary, ResultField } from '@metafora/contracts';
import { get } from '../api.ts';
import * as demo from '../demo.ts';
import { dob, gap, nhsMasked, outcome, stamp, statusPill } from '../format.ts';
import { Link } from '../router.tsx';
import { lines } from '../transcript.ts';

/**
 * One interview, read inside the patient's context.
 *
 * Two panes. Left, the transcript, with every safety scan that ran shown
 * against the turn it ran on — **including the ones that matched nothing**,
 * because that is the only evidence on any screen that the gate ran on a turn
 * it cleared. Right, the review composer, read-only: the impression, the
 * disposition and the signature are Phase 5, and a form that accepts input it
 * cannot store is worse than one that plainly does not yet.
 */
export function Interview({ id }: { id: string }) {
  const [detail, setDetail] = useState<InterviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    // The previous interview stays on screen while the next one loads. Blanking
    // it to "Reading the interview…" was fine when this screen was only ever
    // arrived at from the table; now that the strip below steps between a
    // patient's calls, it made every step a full-page flash.
    get<InterviewDetail>(`/interviews/${id}`)
      .then((value) => live && setDetail(value))
      .catch((e: Error) => {
        if (!live) return;
        // A row that cannot be read must not leave the last one showing under
        // the error, which would read as its explanation.
        setDetail(null);
        setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [id]);

  if (error) {
    return (
      <div className="page">
        <p className="note note--bad">
          <b>That interview is not readable</b>
          {error}
          <br />
          An interview belonging to another clinician answers the same way as one that does not
          exist — telling them apart would confirm the id.
        </p>
      </div>
    );
  }
  if (!detail) return <p className="note">Reading the interview…</p>;

  const row = detail.interview;
  // Between a click on the strip and that interview arriving. Derived from the
  // row rather than kept as a second piece of state, so it cannot disagree with
  // what is actually drawn.
  const opening = row.id !== id;
  const said = outcome(row);
  const pill = statusPill(row);
  // Fetched with the interview rather than filtered out of the dashboard's
  // list, so opening this screen from a bookmark draws the same timeline as
  // reaching it from the table — and so it does not quietly become a timeline
  // of whichever page the table happened to be showing.
  const history = detail.history;
  const born = dob(row.patientDateOfBirth);

  return (
    <>
      <div className="crumb">
        <Link to="/patients">Patients</Link>
        <i>›</i>
        <b>{row.patientFirstName}</b>
        <i>›</i>
        <span>
          {row.protocolLabel} · {stamp(row.endedAt ?? row.createdAt)}
        </span>
      </div>

      <div className={opening ? 'dhead is-stale' : 'dhead'} aria-busy={opening}>
        <span>
          <span className="dhead__n">{row.patientFirstName}</span>
          <span className="dhead__s">
            {nhsMasked(row.patientNhsNumber)} · DOB {born.label}
            {born.age !== null && ` · ${born.age}`}
          </span>
        </span>
        <span className="chips">
          <span className="pill">
            responsible ·{' '}
            {/* Real, and `null` is worth showing: a demo visitor is nobody's
                patient, which is what Phase 1's nullable column encodes. */}
            {history[0] ? 'this deployment' : 'unassigned'}
          </span>
          <span className="pill pill--done">
            <span className="chk">✓</span>
            {demo.consent}
          </span>
          <span className={pill.kind ? `pill pill--${pill.kind}` : 'pill'}>
            {pill.kind === 'danger' && <span className="pill__d" />}
            {pill.label}
          </span>
        </span>
      </div>

      {/* The route's id, not the loaded row's: the lit node moves the instant
          it is clicked, and the content catches up under it. The strip is the
          navigation, so it is the one thing here that never dims. */}
      <Timeline history={history} currentId={id} />

      <div className={opening ? 'panes is-stale' : 'panes'} aria-busy={opening}>
        <Transcript detail={detail} />
        <Composer detail={detail} summary={said.title} />
      </div>
    </>
  );
}

/**
 * The history strip, oldest → newest, with the intervals labelled on the spine.
 * Real: it is every interview this patient has, from the list already loaded.
 * The gaps between a patient's contacts are clinical signal, which is the whole
 * reason this is a timeline and not five equal cards.
 *
 * Every item but the current one is a link to that interview, which is the
 * shortest path between two calls of the same patient — the strip already knew
 * which they were and drew them in order, and the only way to open one was to
 * go back to the review table and find it again by name.
 *
 * The anchor wraps the item's *content* rather than the item itself: the node
 * and the interval are the spine, shared between neighbours, and a link that
 * swallowed them would claim half the gap before it as its own. It also keeps
 * `listitem` on the item, so the anchor is announced as a link rather than
 * having its role overwritten by the one the list needs.
 */
function Timeline({
  history,
  currentId,
}: {
  history: InterviewSummary[];
  currentId: string;
}) {
  if (history.length < 2) return null;

  return (
    <div className="tl" role="list" aria-label="Interview history, oldest first">
      {history.map((row, i) => {
        const at = row.endedAt ?? row.createdAt;
        // The interval *ahead* of this node, not behind it. `.tl__gap` is
        // centred on the item, and an item's span runs from its own node to the
        // next one — so the label a node carries is the one it is sitting at
        // the start of. Measuring backwards drew every interval a slot to the
        // right of itself, and the last item's label landed past the final node
        // on the 6px stub of spine, with nothing on either side of it.
        // `docs/ux/clinical-dashboard.html:1073` has it forwards: a label on
        // the first item and none on the last.
        const next = history[i + 1];
        // Null when there is no interval worth a word — see `gap`.
        const until = next ? gap(at, next.endedAt ?? next.createdAt) : null;
        const now = row.id === currentId;
        const pill = statusPill(row);
        const when = new Date(at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
        const body = (
          <>
            <span className="tl__d">{when}</span>
            <span className="tl__p">{row.protocolLabel}</span>
            <span className={pill.kind ? `pill pill--${pill.kind}` : 'pill'}>
              {pill.kind === 'danger' && <span className="pill__d" />}
              {pill.label}
            </span>
          </>
        );
        return (
          <span
            className={now ? 'tl__i tl__i--now' : 'tl__i'}
            role="listitem"
            // The one being read, for anyone who cannot see which node is lit.
            aria-current={now ? 'true' : undefined}
            key={row.id}
          >
            <span className="tl__node" />
            {until && <span className="tl__gap">{until}</span>}
            {now ? (
              <span className="tl__go">{body}</span>
            ) : (
              // The date and the protocol are the whole label: two interviews a
              // patient had on the same script are told apart by when, and the
              // strip is ordered so "older" and "newer" are already on screen.
              <Link
                className="tl__go"
                to={`/interviews/${row.id}`}
                aria-label={`Open the ${row.protocolLabel} of ${when}`}
              >
                {body}
              </Link>
            )}
            {now && <span className="tl__owed">Awaiting your review</span>}
          </span>
        );
      })}
    </div>
  );
}

function Transcript({ detail }: { detail: InterviewDetail }) {
  const [tab, setTab] = useState<'transcript' | 'record'>('transcript');
  const spoken = lines(detail.events);
  const captured = detail.results.filter((r) => r.status === 'captured').length;

  return (
    <div className="pane">
      <div className="tabs">
        <span
          className={tab === 'transcript' ? 'tab tab--on' : 'tab'}
          role="button"
          tabIndex={0}
          onClick={() => setTab('transcript')}
        >
          Transcript
        </span>
        <span
          className={tab === 'record' ? 'tab tab--on' : 'tab'}
          role="button"
          tabIndex={0}
          onClick={() => setTab('record')}
        >
          Record {captured}/{detail.results.length}
        </span>
        {/* Phase 5 and later: `clinical.signatures` and audio retention. */}
        <span className="tab is-inert" title="The signature ledger arrives in Phase 5">
          Ledger
        </span>
        <span className="tab is-inert" title="Audio retention is not built">
          Audio
        </span>
      </div>

      {tab === 'record' ? (
        <Record results={detail.results} />
      ) : spoken.length === 0 ? (
        <p className="note">
          <b>Nothing was said</b>
          The call has no committed turns — it was queued, or it ended before the patient answered.
        </p>
      ) : (
        spoken.map((line) => (
          <div className="tline" key={line.seq}>
            <span className="tline__t">
              {new Date(line.at).toLocaleTimeString('en-GB', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
              })}
            </span>
            <span>
              <span className="tline__w">{line.who}</span>
              {line.text && <span className="tline__x">{line.text}</span>}
              {line.scan && (
                <span className={line.scan.hit ? 'scan scan--hit' : 'scan'}>{line.scan.text}</span>
              )}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function Record({ results }: { results: ResultField[] }) {
  if (results.length === 0) {
    return (
      <p className="note">
        <b>Nothing captured</b>
        `clinical.results` is written when the call ends, so a running interview has no rows yet.
      </p>
    );
  }
  return (
    <div className="cmp__b">
      {results.map((field) => (
        <span key={field.fieldKey}>
          <span className="fld__l">
            {field.label} · {field.status}
          </span>
          <span className={field.value ? 'fld__v' : 'fld__v fld__v--ro'}>
            {field.value ?? 'not answered'}
          </span>
        </span>
      ))}
    </div>
  );
}

/**
 * The review composer, read-only.
 *
 * `docs/roadmap.md` §4 is the read path, and every control here writes: the
 * impression, the disposition and the Sign that pins a record hash to a ledger
 * head. `clinical.signatures` and its append-only `(prev_hash, record_hash,
 * hash)` chain are Phase 5, so the hashes below are shaped like what will
 * replace them and are otherwise `demo.ts`. Sign is disabled rather than
 * hidden, because the shape of the act is the argument this screen is making.
 */
function Composer({ detail, summary }: { detail: InterviewDetail; summary: string }) {
  const row = detail.interview;
  const hashes = demo.hashes(row.id);
  const captured = detail.results.filter((r) => r.status === 'captured').length;
  const born = dob(row.patientDateOfBirth);

  return (
    <div className="cmp">
      <div className="cmp__h">Review composer</div>
      <div className="cmp__b">
        <span>
          <span className="fld__l">Issued summary · composed from the record</span>
          <span className="fld__v fld__v--ro">
            {born.age !== null && `${born.age}-year-old, `}
            {row.protocolLabel.toLowerCase()} for {demo.referral(row.patientId)}.{' '}
            {summary}
            {row.endedAt ? ` at ${stamp(row.endedAt)}` : ''}; {captured} of {detail.results.length}{' '}
            declared items captured.
          </span>
        </span>
        <span>
          <span className="fld__l">Clinical impression</span>
          <textarea
            className="fld__v fld__v--type"
            rows={3}
            placeholder="Writing back to the record arrives with the signature ledger."
            disabled
          />
        </span>
        <span>
          <span className="fld__l">Disposition</span>
          <select className="fld__v" disabled>
            <option>same_day</option>
          </select>
        </span>
      </div>
      <div className="sig">
        <span className="sig__h">
          record {hashes.record} · ledger head {hashes.head}
        </span>
        <div className="sig__r">
          <span className="sig__note">Signing is irreversible</span>
          <button className="btn-grad" type="button" disabled title="Sign-off arrives in Phase 5">
            Sign
          </button>
        </div>
      </div>
    </div>
  );
}
