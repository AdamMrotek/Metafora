import { useEffect, useMemo, useState } from 'react';
import type { DispatchRequest, InterviewSummary, ProtocolOption } from '@metafora/contracts';
import { get, post } from '../api.ts';
import { CopyLink } from '../CopyLink.tsx';
import { useRecord } from '../data.tsx';
import { nhsMasked, relative, stamp } from '../format.ts';
import { navigate } from '../router.tsx';

/**
 * Send an interview.
 *
 * The spec's compose panel (`docs/ux/clinical-dashboard.html`) is four answers
 * to one question, side by side: *who · what · when · how*. Nothing else is
 * asked, because nothing else is a clinician's decision here.
 *
 * Two of the four are not what the mockup drew, and both differences are the
 * product being honest about what it holds:
 *
 * - *who* offers a first name and nothing more for someone new. This product
 *   collects no demographics and `docs/system-map.md` forbids inventing any —
 *   the identity a real deployment carries across at dispatch comes from a
 *   system that already has it, and there is no such system here.
 * - *how* is fixed to a link. The mockup says "Voice call · mobile"; Phase 5a
 *   drops the email channel deliberately (a provider is unchosen and would be a
 *   fourth egress), so the clinician copies the URL and sends it themselves.
 *   The send row says exactly that rather than implying a dial-out.
 */
export function Deployments() {
  const { interviews, patients, reload } = useRecord();
  const [protocols, setProtocols] = useState<ProtocolOption[]>([]);

  const [patientId, setPatientId] = useState('');
  const [firstName, setFirstName] = useState('');
  const [protocolId, setProtocolId] = useState('');
  const [slot, setSlot] = useState('');

  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<InterviewSummary | null>(null);

  useEffect(() => {
    let live = true;
    get<ProtocolOption[]>('/protocols')
      .then((list) => {
        if (!live) return;
        setProtocols(list);
        // The first is the default rather than an empty option, because there
        // is no such thing as an interview with no script and a select that
        // starts on "choose one" is a step nobody needed.
        setProtocolId((current) => current || (list[0]?.id ?? ''));
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  const roster = useMemo(
    () => [...patients].sort((a, b) => a.firstName.localeCompare(b.firstName)),
    [patients],
  );

  const upcoming = useMemo(
    () =>
      interviews
        .filter((row) => row.status === 'queued')
        .sort((a, b) =>
          (a.scheduledFor ?? a.createdAt).localeCompare(b.scheduledFor ?? b.createdAt),
        ),
    [interviews],
  );

  const naming = patientId === 'new' ? firstName.trim() : patientId;
  const chosen = roster.find((p) => p.id === patientId);
  const who = patientId === 'new' ? firstName.trim() : (chosen?.firstName ?? '');
  const ready = naming !== '' && protocolId !== '' && !sending;

  async function send() {
    if (!ready) return;
    setSending(true);
    setError(null);
    try {
      const body: DispatchRequest = {
        protocolId,
        // Exactly one of the two, which is what the backend asserts as well.
        ...(patientId === 'new' ? { firstName: firstName.trim() } : { patientId }),
        // A `datetime-local` value carries no zone; the browser's is the right
        // one, because the clinician chose the slot in it.
        ...(slot ? { scheduledFor: new Date(slot).toISOString() } : {}),
      };
      const summary = await post<InterviewSummary>('/interviews', body);
      setSent(summary);
      setFirstName('');
      setSlot('');
      // The row is now on the dashboard's scheduled card and its review table,
      // so the two lists everything else reads have to be refetched.
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'the call could not be queued');
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page">
      <div className="greet">
        <b>Deployments</b>
        <span>
          {upcoming.length} {upcoming.length === 1 ? 'call' : 'calls'} waiting on a patient ·
          nothing is sent until you pass on the link
        </span>
      </div>

      <div className="panel">
        <div className="toolbar">
          <span className="toolbar__t">Send an interview</span>
          <span className="toolbar__c">nothing is sent until you press send</span>
        </div>

        <div className="dep">
          <span className="fld">
            <label className="fld__l" htmlFor="dep-who">
              Who · patient
            </label>
            <select
              id="dep-who"
              className="fld__v"
              value={patientId}
              onChange={(e) => setPatientId(e.target.value)}
            >
              <option value="">Choose a patient</option>
              <option value="new">New patient · first name only</option>
              {roster.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.firstName} · {nhsMasked(p.nhsNumber)}
                </option>
              ))}
            </select>
            {patientId === 'new' && (
              <input
                className="fld__v dep__new"
                placeholder="First name"
                aria-label="The new patient’s first name"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
              />
            )}
          </span>

          <span className="fld">
            <label className="fld__l" htmlFor="dep-what">
              What · call type
            </label>
            <select
              id="dep-what"
              className="fld__v"
              value={protocolId}
              onChange={(e) => setProtocolId(e.target.value)}
            >
              {protocols.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </span>

          <span className="fld">
            <label className="fld__l" htmlFor="dep-when">
              When · slot
            </label>
            <input
              id="dep-when"
              className="fld__v"
              type="datetime-local"
              value={slot}
              onChange={(e) => setSlot(e.target.value)}
            />
            <span className="dep__note">
              {slot ? 'the call is for this time' : 'optional · they can call whenever'}
            </span>
          </span>

          <span className="fld">
            <span className="fld__l">How · delivery</span>
            {/* Not a select with one option: a control that cannot be changed
                should not look like one that can. */}
            <span className="fld__v fld__v--ro">Link</span>
            <span className="dep__note">you copy it and send it</span>
          </span>
        </div>

        <div className="send">
          <span className="send__t">
            {who ? (
              <>
                <b>Queues an interview for {who}.</b> Nothing is dialled and nothing is emailed —
                you copy the link below and pass it on. It is spent the moment they start the
                call.
              </>
            ) : (
              <>
                <b>Choose who the call is for.</b> Queueing it mints one link, which you copy and
                send however you like. Nothing leaves this system on its own.
              </>
            )}
          </span>
          <button className="btn-grad" type="button" onClick={send} disabled={!ready}>
            {sending ? 'Queueing…' : 'Send'}
          </button>
        </div>

        {error && (
          <p className="note note--bad">
            <b>The call was not queued</b>
            {error}
          </p>
        )}

        {sent && (
          <p className="note note--sent">
            <b>Queued for {sent.patientFirstName}</b>
            {sent.protocolLabel} ·{' '}
            {sent.scheduledFor ? `for ${stamp(sent.scheduledFor)}` : 'no slot — whenever they can'}
            <span className="note__act">
              <CopyLink interviewId={sent.id} label={`Copy ${sent.patientFirstName}’s link`} />
              copy the link and send it to them
            </span>
          </p>
        )}
      </div>

      <div className="panel">
        <div className="toolbar">
          <span className="toolbar__t">Upcoming</span>
          <span className="toolbar__c">
            {upcoming.length} waiting on a patient
          </span>
        </div>

        {upcoming.length === 0 ? (
          <p className="note">
            <b>Nothing is out</b>
            Every call you queue appears here until the patient starts it.
          </p>
        ) : (
          <table className="dt">
            <colgroup>
              <col style={{ width: '24%' }} />
              <col style={{ width: '26%' }} />
              <col style={{ width: '22%' }} />
              <col style={{ width: '14%' }} />
              <col style={{ width: '14%' }} />
            </colgroup>
            <thead>
              <tr>
                <th>Patient</th>
                <th>Call type</th>
                <th className="sorted">Slot ↑</th>
                <th>Delivery</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody>
              {upcoming.map((row) => (
                <tr key={row.id} data-href={`/interviews/${row.id}`}>
                  <td onClick={() => navigate(`/interviews/${row.id}`)}>
                    <span className="who">{row.patientFirstName}</span>
                    <span className="sub mono">{nhsMasked(row.patientNhsNumber)}</span>
                  </td>
                  <td>
                    {row.protocolLabel}{' '}
                    <span className="ver">{row.protocolId.split('_').pop()}</span>
                  </td>
                  <td className="mono">
                    {row.scheduledFor ? stamp(row.scheduledFor) : '–'}
                    <span className="sub">
                      {row.scheduledFor ? relative(row.scheduledFor) : 'no slot'}
                    </span>
                  </td>
                  <td>
                    <span className="pill pill--faint">link</span>
                  </td>
                  <td>
                    <CopyLink
                      interviewId={row.id}
                      label={`Copy ${row.patientFirstName}’s link`}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
