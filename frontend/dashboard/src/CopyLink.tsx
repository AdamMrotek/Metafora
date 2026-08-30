import { useEffect, useRef, useState } from 'react';
import type { Invitation } from '@metafora/contracts';
import { post } from './api.ts';

/**
 * Copy the link for one interview.
 *
 * Phase 5a drops the email channel deliberately — a provider is unchosen and
 * would be a fourth egress — so this button *is* the send: the clinician takes
 * the URL and passes it on however they like. Everything behind it is real, so
 * a `services/comms/` later is a sender rather than a redesign.
 *
 * Two things it does not do, and both are the backend's job by design. It does
 * not build the URL — `POST /interviews/{id}/invitation` returns the whole
 * thing, because where the patient portal lives is deployment configuration and
 * a bundle that baked it in would need a rebuild to move it. And it does not
 * decide whether to mint: a second click returns the *same* link, because the
 * first may already be with a patient.
 */
export function CopyLink({ interviewId, label }: { interviewId: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'working' | 'copied'>('idle');
  const [error, setError] = useState<string | null>(null);
  // Set when the clipboard is unavailable — an insecure context, or a browser
  // that refuses without a user gesture it recognised. The URL is still real,
  // so it is shown to be selected by hand rather than thrown away.
  const [manual, setManual] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  async function copy(event: React.MouseEvent) {
    // Safe to sit inside a click-to-navigate row: the escalation rail and the
    // review table both make whole rows clickable, and this button will end up
    // in one of them.
    event.stopPropagation();
    event.preventDefault();
    if (state === 'working') return;

    setState('working');
    setError(null);
    setManual(null);
    try {
      const invitation = await post<Invitation>(`/interviews/${interviewId}/invitation`);
      try {
        await navigator.clipboard.writeText(invitation.url);
        setState('copied');
        timer.current = setTimeout(() => setState('idle'), 1500);
      } catch {
        setManual(invitation.url);
        setState('idle');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not mint a link');
      setState('idle');
    }
  }

  return (
    <span className="copy">
      <button
        className="icon-btn"
        type="button"
        onClick={copy}
        disabled={state === 'working'}
        aria-label={label ?? 'Copy the patient’s link'}
        title={label ?? 'Copy the patient’s link'}
      >
        <svg
          viewBox="0 0 24 24"
          width="15"
          height="15"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      </button>
      {/* Announced rather than only drawn: the button's own label does not
          change, so a screen reader would otherwise get no confirmation. */}
      <span className={state === 'copied' ? 'copy__ok copy__ok--on' : 'copy__ok'} aria-live="polite">
        {state === 'copied' ? 'Copied' : ''}
      </span>
      {manual && (
        <input
          className="copy__manual"
          readOnly
          value={manual}
          aria-label="The patient’s link — select and copy it"
          onClick={(e) => e.currentTarget.select()}
          onFocus={(e) => e.currentTarget.select()}
        />
      )}
      {error && <span className="copy__bad">{error}</span>}
    </span>
  );
}
