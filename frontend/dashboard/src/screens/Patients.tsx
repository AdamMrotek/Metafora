import { useMemo, useState } from 'react';
import type { PatientSummary } from '@metafora/contracts';
import { useRecord } from '../data.tsx';
import { dob, nhsMasked, relative, stamp } from '../format.ts';
import { Link, navigate } from '../router.tsx';

/**
 * Who is on my list.
 *
 * `reads.patients` returns this clinician's own rows plus the unowned demo
 * ones, each carrying `origin` — so a person dispatched a call is
 * distinguishable from someone who clicked a public link, which is the
 * difference between a caseload and a caseload full of strangers.
 *
 * Search is client-side over a bounded list rather than a route parameter,
 * because the route is capped at 100 rows and a second query for a substring
 * would be a round trip to filter something already in memory.
 */
export function Patients() {
  const { patients, loading, error } = useRecord();
  const [query, setQuery] = useState('');

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    // NHS numbers are read aloud and written down in threes, so the spaces a
    // clinician types are theirs and not the record's.
    const digits = needle.replace(/\D/g, '');
    // No sort: `reads.patients` already orders by when something last happened
    // to each person, so this narrows a list rather than reordering one.
    return patients.filter(
      (p) =>
        !needle ||
        p.firstName.toLowerCase().includes(needle) ||
        (digits !== '' && (p.nhsNumber ?? '').includes(digits)),
    );
  }, [patients, query]);

  const withSomethingOpen = patients.filter((p) => p.openCount > 0).length;

  return (
    <div className="page">
      <div className="greet">
        <b>Patients</b>
        <span>
          {patients.length} on your list · {withSomethingOpen} with something open
        </span>
      </div>

      <div className="panel">
        <div className="toolbar">
          <input
            className="ctl ctl--search"
            placeholder="Name, or NHS number"
            aria-label="Search patients"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <span className="grow" />
          {/* Creating a patient is dispatch — Phase 5 — and it is the act that
              sets `clinician_email`, which is the column every scope in
              `reads.py` turns on. */}
          <button className="btn-grad" type="button" disabled title="Adding a patient arrives with dispatch, in Phase 5">
            Add patient
          </button>
        </div>

        {error ? (
          <p className="note note--bad">
            <b>The list could not be read</b>
            {error}
          </p>
        ) : loading ? (
          <p className="note">Reading your list…</p>
        ) : rows.length === 0 ? (
          <p className="note">
            <b>Nobody here</b>
            {patients.length === 0
              ? 'No patient is assigned to you, and no demo visitor has started a call.'
              : 'No patient matches that search.'}
          </p>
        ) : (
          <table className="dt">
            <colgroup>
              <col style={{ width: '32%' }} />
              <col style={{ width: '30%' }} />
              <col style={{ width: '20%' }} />
              <col style={{ width: '18%' }} />
            </colgroup>
            <thead>
              <tr>
                <th>Patient</th>
                <th>Last interview</th>
                <th>Next scheduled</th>
                <th className="sorted">Open</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((patient) => (
                <Row key={patient.id} patient={patient} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Row({ patient }: { patient: PatientSummary }) {
  // Every one of these is a column on the row now. They used to be derived
  // here by filtering the dashboard's shared interview list, which stopped
  // being possible when that list became one page of the review table — and
  // was already wrong before it, because a "2 open" pill counted from a
  // hundred-row window is a number that changes when you page.
  const open = patient.openCount;
  // There is no patient profile screen in this phase, so the arrow opens the
  // most recent interview — which is what someone reaching for the row wants
  // nine times in ten.
  const href = patient.lastInterviewId ? `/interviews/${patient.lastInterviewId}` : undefined;
  const born = dob(patient.dateOfBirth);

  return (
    <tr data-href={href} onClick={() => href && navigate(href)}>
      <td>
        <span className="who">{patient.firstName}</span>
        <span className="sub mono">
          {nhsMasked(patient.nhsNumber)} · {born.label}
          {patient.origin === 'demo' && ' · demo patient'}
        </span>
      </td>
      <td>
        {patient.lastProtocolLabel && patient.lastInterviewAt ? (
          <>
            {patient.lastProtocolLabel}{' '}
            <span className="ver">{patient.lastProtocolId?.split('_').pop()}</span>
            <span className="rel">
              {stamp(patient.lastInterviewAt)} · {relative(patient.lastInterviewAt)}
            </span>
          </>
        ) : (
          <>
            –<span className="rel">never interviewed</span>
          </>
        )}
      </td>
      <td>
        {patient.nextScheduledFor ? (
          <>
            {stamp(patient.nextScheduledFor)}
            <span className="rel">invited, not started</span>
          </>
        ) : (
          <>
            –<span className="rel">nothing pending</span>
          </>
        )}
      </td>
      <td>
        <span className="status">
          <span
            className={
              open === 0
                ? 'pill pill--faint'
                : patient.hasEscalation
                  ? 'pill pill--danger'
                  : 'pill pill--warn'
            }
          >
            {open === 0 ? 'none' : `${open} open`}
          </span>
          {href && (
            <Link className="act-link" to={href} aria-label={`Open ${patient.firstName}'s last interview`}>
              →
            </Link>
          )}
        </span>
      </td>
    </tr>
  );
}
