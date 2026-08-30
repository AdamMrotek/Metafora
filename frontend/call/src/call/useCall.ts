import type {
  CallPhase,
  FieldState,
  ServerMessage,
  SessionBootstrap,
} from '@metafora/contracts';
import { decodeMessage } from '@metafora/contracts';
import { RoomEvent, Room, Track } from 'livekit-client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useMic } from './useMic.ts';

/**
 * How long to keep playing after the assistant *leaves the room*.
 *
 * The bot drops its transport only once the pipeline has genuinely finished
 * speaking (`services/core/app.py`, the `finally` in `run_session`), so its
 * departure — not "ended" — is the signal that the audio is complete. What is
 * left at that moment is the tail sitting in our own jitter buffer, which is
 * milliseconds, not sentences.
 */
const JITTER_DRAIN_MS = 750;

/**
 * Backstop for an assistant that never leaves: a hard-killed server, a dropped
 * network. Must stay above `GOODBYE_TIMEOUT_S` in `services/core/app.py`, which
 * is how long that side will wait for the pipeline to stop speaking — if this
 * fires first we clip exactly the goodbye it was holding the line for.
 */
const MAX_DRAIN_MS = 12_000;

/** What `mint_token(..., "assistant", ...)` in `services/core/app.py` names it. */
const ASSISTANT_IDENTITY = 'assistant';

/** A refusal from `POST /session`, carrying whether trying again could help. */
class StartRefused extends Error {
  constructor(
    message: string,
    readonly final: boolean,
  ) {
    super(message);
  }
}

export interface Bubble {
  id: string;
  who: 'assistant' | 'patient';
  text: string;
}

export interface CallState {
  phase: CallPhase;
  bubbles: Bubble[];
  fields: FieldState[];
  session: SessionBootstrap | null;
  error: string | null;
  connecting: boolean;
  ended: boolean;
  /**
   * The call was refused for a reason no amount of trying again will fix —
   * a spent link, or a deployment that is invitation-only. The distinction
   * matters on the opening screen: everything else there is worth another tap,
   * and this is a sentence with no button under it.
   */
  blocked: boolean;
}

/**
 * The call, from the patient's side.
 *
 * Everything on screen is told to us by the assistant over the data channel.
 * In particular the call bar reflects the *server's* phase rather than
 * anything inferred locally — the server is the only party that knows whether
 * it is still generating, and a bar that guesses is a bar that lies.
 */
export function useCall() {
  const mic = useMic();
  const roomRef = useRef<Room | null>(null);
  const drainRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backstopRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [state, setState] = useState<CallState>({
    phase: 'idle',
    bubbles: [],
    fields: [],
    session: null,
    error: null,
    connecting: false,
    ended: false,
    blocked: false,
  });

  /**
   * Close the line: the microphone and the room both.
   *
   * Safe to call twice — the room is cleared before it is disconnected, so the
   * Disconnected event this raises does not come back round for a second go.
   */
  const hangUp = useCallback(() => {
    for (const ref of [drainRef, backstopRef]) {
      if (ref.current !== null) {
        clearTimeout(ref.current);
        ref.current = null;
      }
    }
    mic.release();
    const room = roomRef.current;
    roomRef.current = null;
    void room?.disconnect();
  }, [mic]);

  // Leaving the screen mid-call ends the call.
  useEffect(() => hangUp, [hangUp]);

  const apply = useCallback(
    (message: ServerMessage) => {
      if (message.t === 'ended') {
        // Nothing the patient says now is wanted, so the mic goes at once.
        //
        // The line itself stays open. "ended" is sent *before* the assistant
        // stops speaking — on a server shutdown, mid-sentence with a whole
        // utterance still to play — so hanging up on a timer started here
        // clips whatever is left. We wait for the assistant to leave instead,
        // and only arm a backstop in case it never does.
        mic.release();
        backstopRef.current ??= setTimeout(hangUp, MAX_DRAIN_MS);
      }

      setState((prev) => {
        switch (message.t) {
          case 'utterance': {
            // Ids are stable, so a repeated message replaces rather than repeats.
            const without = prev.bubbles.filter((b) => b.id !== message.id);
            return {
              ...prev,
              bubbles: [...without, { id: message.id, who: message.who, text: message.text }],
            };
          }
          case 'phase':
            return { ...prev, phase: message.phase };
          case 'notes':
            return { ...prev, fields: message.fields };
          case 'ended':
            return { ...prev, ended: true, phase: 'ended' };
          default:
            return prev;
        }
      });
    },
    [hangUp, mic],
  );

  const start = useCallback(async () => {
    setState((s) => ({ ...s, connecting: true, error: null, blocked: false }));
    try {
      // ── 1 · the microphone, before anything else ──
      // The assistant starts talking the moment a session exists, so asking
      // for the mic first means no patient is ever greeted by an assistant
      // that cannot hear them, and no orphan session is created if they say no.
      const micTrack = await mic.open();

      // ── 2 · the session ──
      //
      // The invitation, if there is one, is a query parameter rather than a
      // path: `frontend/call/vercel.json` has no SPA rewrite, and a query needs
      // none. Absent, the body is `{ invite: null }`, which is what the public
      // demo has always meant — `SessionStart` defaults every field.
      const invite = new URLSearchParams(window.location.search).get('invite');
      const res = await fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invite }),
      });
      if (!res.ok) {
        const message = (await res.json().catch(() => ({}))).error ?? 'could not start';
        // 403 is "this deployment is invitation-only"; 404 with a link is a
        // link already spent. Neither is retryable, and the opening screen says
        // so instead of offering the button again.
        throw new StartRefused(message, res.status === 403 || res.status === 404);
      }
      const { token, url, session } = (await res.json()) as {
        token: string;
        url: string;
        session: SessionBootstrap;
      };

      // ── 3 · the room ──
      const room = new Room();
      roomRef.current = room;

      room.on(RoomEvent.DataReceived, (payload) => apply(decodeMessage<ServerMessage>(payload)));
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) track.attach();
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });
      // The assistant leaving is the only trustworthy "the audio is finished"
      // signal we get: the bot holds its transport open until the pipeline has
      // stopped speaking. Whatever is left after that is jitter buffer, so we
      // drain that and close — no assistant means no call, whether or not
      // "ended" ever arrived.
      room.on(RoomEvent.ParticipantDisconnected, (participant) => {
        if (participant.identity !== ASSISTANT_IDENTITY) return;
        drainRef.current ??= setTimeout(hangUp, JITTER_DRAIN_MS);
      });
      // However the line drops — the bot leaving, the network, our own
      // hangup — the microphone goes with it.
      room.on(RoomEvent.Disconnected, () => {
        hangUp();
        setState((s) => ({ ...s, phase: s.ended ? 'ended' : 'idle' }));
      });

      await room.connect(url, token);
      // The line opens once and stays open. There is no push-to-talk here.
      await room.localParticipant.publishTrack(micTrack, { source: Track.Source.Microphone });

      setState((s) => ({
        ...s,
        session,
        fields: session.fields,
        connecting: false,
        phase: 'listening',
      }));
    } catch (err) {
      // The mic may already be open: the session or the room can fail after
      // the patient has said yes to it.
      hangUp();
      setState((s) => ({
        ...s,
        connecting: false,
        error: err instanceof Error ? err.message : 'could not start the call',
        blocked: err instanceof StartRefused && err.final,
      }));
    }
  }, [apply, hangUp, mic]);

  const sendTyped = useCallback(
    async (text: string) => {
      const id = state.session?.sessionId;
      const machineId = state.session?.machineId;
      if (!id || !text.trim()) return;
      await fetch(`/api/session/${id}/typed`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // The session lives in one backend process's memory, so this has to
          // come back to the machine that started the call. Absent in dev and
          // wherever there is only one, which is why it is spread rather than
          // set: an empty header is not the same as no header.
          ...(machineId ? { 'fly-force-instance-id': machineId } : {}),
        },
        body: JSON.stringify({ text }),
      });
    },
    [state.session?.sessionId, state.session?.machineId],
  );

  return { ...state, start, sendTyped };
}
