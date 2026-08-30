import { useEffect, useRef, useState } from 'react';
import { CallBar } from './call/CallBar.tsx';
import { Notes } from './call/Notes.tsx';
import { useCall } from './call/useCall.ts';

/**
 * The patient portal.
 *
 * Three rules hold this screen together:
 *   ① one column of conversation and nothing competing with it;
 *   ② the answer control is the largest thing on the page at all times;
 *   ③ what has been understood is visible without being asked for.
 *
 * What it does not have: a question counter, a timer, a progress bar, a Next
 * button, or a way to push to talk. "Question 7 of 16" is the sentence that
 * turns a conversation back into a form.
 */
export function App() {
  const call = useCall();
  const started = call.session !== null;

  return (
    <div className="app">
      <Header
        clinician={call.session?.clinician.name ?? 'Dr E. Hollis'}
        initials={call.session?.clinician.initials ?? 'EH'}
        practice={call.session?.clinician.practice ?? 'Springfield Practice'}
        context={call.session?.clinician.context ?? 'a quick chat before your appointment'}
      />

      {!started ? (
        <Opening
          onStart={call.start}
          connecting={call.connecting}
          error={call.error}
          blocked={call.blocked}
        />
      ) : (
        <div className="pp__body">
          <div className="pp__conv">
            <Thread bubbles={call.bubbles} phase={call.phase} />
            {call.error && <p className="pp__error">{call.error}</p>}
            <Answer phase={call.phase} onTyped={call.sendTyped} ended={call.ended} />
          </div>
          <Notes fields={call.fields} clinician={call.session!.clinician.name} />
        </div>
      )}
    </div>
  );
}

/**
 * The header carries a person's name, because "who is asking me this?" is the
 * first thing a patient thinks and the last thing an intake form usually
 * answers. Nothing sits on the right: no question count, no elapsed time, no
 * connection chip. A running clock is the sentence that tells someone to hurry.
 */
function Header(props: { clinician: string; initials: string; practice: string; context: string }) {
  return (
    <div className="pp__head">
      <span className="pp__av">{props.initials}</span>
      <span className="pp__who">
        <b>{props.clinician}</b>
        <span>
          {props.practice} · {props.context}
        </span>
      </span>
    </div>
  );
}

/**
 * The only tap in the whole interview, and the only filled button on screen.
 *
 * Unless there is nothing to tap. A link that has been used, or a deployment
 * that is invitation-only, is a refusal no amount of trying again resolves —
 * so the sentence the server wrote stands where the button would be, rather
 * than under a button that will fail identically.
 */
function Opening({
  onStart,
  connecting,
  error,
  blocked,
}: {
  onStart: () => void;
  connecting: boolean;
  error: string | null;
  blocked: boolean;
}) {
  if (blocked) {
    return (
      <div className="pp__start">
        <p>{error}</p>
        <p className="pp__demo">
          If you were expecting to take a call, ask whoever sent you the link for a new one.
        </p>
      </div>
    );
  }

  return (
    <div className="pp__start">
      <p>
        When you are ready, start the conversation. There is nothing to press after this — just talk
        normally, and jump in whenever you like.
      </p>
      <button className="speak" type="button" onClick={onStart} disabled={connecting}>
        <svg
          viewBox="0 0 24 24"
          width="17"
          height="17"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M4.5 5.5A2.5 2.5 0 0 1 7 3h1.7l1.3 4-2 1.4a11 11 0 0 0 5.6 5.6L15 12l4 1.3V15a2.5 2.5 0 0 1-2.5 2.5A12 12 0 0 1 4.5 5.5Z" />
        </svg>
        {connecting ? 'Connecting…' : 'Start the conversation'}
      </button>
      {error && <p className="pp__error">{error}</p>}
      {/*
        Below the button on purpose. Rule ② is that the answer control is the
        largest thing on the page, and putting this above it would make the
        first thing a patient reads a disclaimer. But it has to be here and it
        has to be a sentence: this is a public demonstration, nobody is on the
        other end of it, and someone will otherwise tell it something true.
      */}
      <p className="pp__demo">
        This is a demonstration on made-up data. Please don’t enter real personal or medical
        details — nobody is monitoring this conversation.
      </p>
    </div>
  );
}

function Thread({
  bubbles,
  phase,
}: {
  bubbles: { id: string; who: 'assistant' | 'patient'; text: string }[];
  phase: string;
}) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [bubbles.length, phase]);

  return (
    <div className="thread">
      <div className="thread__spacer" />
      {bubbles.map((bubble, i) => {
        const live = i === bubbles.length - 1 && bubble.who === 'assistant' && phase === 'speaking';
        return bubble.who === 'assistant' ? (
          <div key={bubble.id} className={live ? 'bub bub--ask bub--live' : 'bub bub--ask'}>
            {bubble.text}
          </div>
        ) : (
          <Said key={bubble.id} text={bubble.text} />
        );
      })}
      <div ref={end} />
    </div>
  );
}

function Said({ text }: { text: string }) {
  return (
    <>
      <div className="bub bub--said">{text}</div>
      <span className="bub__m">You · spoken</span>
    </>
  );
}

/**
 * The bar below the thread is a status, not a control, and it never moves.
 * The typed fallback is offered quietly, because some things are easier
 * written than said out loud.
 */
function Answer({
  phase,
  onTyped,
  ended,
}: {
  phase: string;
  onTyped: (text: string) => void;
  ended: boolean;
}) {
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState('');

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onTyped(draft);
    setDraft('');
    setTyping(false);
  };

  return (
    <div className="answer">
      <div className="call">
        <CallBar phase={phase as never} />
        <span className="grow" />
      </div>

      {!ended &&
        (typing ? (
          <form className="typed" onSubmit={submit}>
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Type your answer"
              aria-label="Type your answer"
            />
            <button type="submit">Send</button>
          </form>
        ) : (
          <span className="answer__alt">
            or{' '}
            <u role="button" tabIndex={0} onClick={() => setTyping(true)}>
              type instead
            </u>
          </span>
        ))}
    </div>
  );
}
