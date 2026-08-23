# Patient portal — `frontend/call/`

The patient-facing half of the product. ~500 lines of React; the design spec it
implements is `docs/ux/patient-portal.html`.

## Who uses it

A patient, on their own phone or laptop, following a link before an
appointment. One session, one sitting, no login and no account. They never see
it again afterwards — the clinician reads the result.

## What it does

A single screen: a voice conversation with the assistant, written down as it
happens.

- **Header** — the clinician's name and practice, so "who is asking me this?"
  is answered first. Nothing on the right: no counter, no timer, no clock.
- **Thread** — the assistant's questions and the patient's spoken answers as
  bubbles, one column, newest scrolled into view.
- **Call bar** — a *status*, not a control: `listening` / `speaking · talk over
  me` / `one moment` / `ended`. No push-to-talk; the line opens once and stays
  open, and the patient can interrupt.
- **Notes so far** — the right quarter, the fields the assistant has captured,
  filling in live. It answers "did that count?" without being asked. Every item
  is a field on the clinician's review.
- **Type instead** — a quiet text fallback for anything easier written than
  said (`POST /session/{id}/typed`).

There is no Next button, no submit, and no navigation. Talking is the only
thing that moves it forward; the call ends on a sentence from the assistant.

## How it is wired

| | |
|---|---|
| `src/App.tsx` | the whole screen — header, thread, answer bar |
| `src/call/useCall.ts` | all the state: mic → `POST /session` → LiveKit room |
| `src/call/CallBar.tsx` | the four phases |
| `src/call/Notes.tsx` | the notes card |
| `@metafora/ui/tokens.css` | tokens *and* every class used here |

Start: ask for the microphone **first** (so nobody is greeted by an assistant
that cannot hear them), then `POST /session` for a LiveKit token, join the
room, publish the mic. Browser echo cancellation is what stops the assistant
interrupting itself.

After that the browser renders only what it is told. Every update arrives on
the LiveKit data channel as one of four messages from `shared/contracts/wire.py`
— `utterance`, `phase`, `notes`, `ended` — and the client infers nothing;
in particular the call bar shows the *server's* phase, because only the server
knows whether it is still generating.

Dev: `vite` on :5173, `/api/*` proxied to the backend on :3000. `make dev`
starts it alongside the SFU and backend.
