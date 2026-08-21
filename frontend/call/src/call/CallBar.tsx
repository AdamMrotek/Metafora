import type { CallPhase } from '@metafora/contracts';

/**
 * The call bar.
 *
 * There is no push-to-talk here, and that is the single most important thing
 * about this screen. The line is open for the whole interview, so this is not
 * a control you operate to speak — it is a report on the line.
 *
 * Live states are outlined rather than filled: a filled pill reads as "press
 * me", and nothing here wants pressing.
 */
export function CallBar({ phase }: { phase: CallPhase }) {
  if (phase === 'speaking') {
    return (
      <span className="speak speak--talk speak--in">
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M4 9v6h3.5L13 19V5L7.5 9H4Z" />
          <path d="M16.5 9.5a3.5 3.5 0 0 1 0 5" />
          <path d="M19 7a7 7 0 0 1 0 10" />
        </svg>
        {/* Interrupting is expected, so the label says so. */}
        Speaking · talk over me
      </span>
    );
  }

  if (phase === 'thinking') {
    return (
      <span className="speak speak--live speak--in">
        <span className="dots">
          <i />
          <i />
          <i />
        </span>
        One moment
      </span>
    );
  }

  if (phase === 'ended') {
    return <span className="speak speak--talk speak--in">The call has ended</span>;
  }

  return (
    <span className="speak speak--live speak--in">
      {/* The bars move with the voice, so "is it hearing me?" is answered
          without a word of instruction. */}
      <span className="wave">
        <i />
        <i />
        <i />
        <i />
        <i />
      </span>
      Listening
    </span>
  );
}
