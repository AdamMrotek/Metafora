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
 * How long the line stays open after the assistant's last word.
 *
 * "ended" travels the data channel, which overtakes the audio still sitting in
 * the jitter buffer — hang up the moment it lands and the goodbye is clipped
 * mid-word. Nothing is sent or heard during the drain except the tail of a
 * sentence already on its way, so this costs the patient nothing.
 */
const DRAIN_MS = 2500;

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
  const [state, setState] = useState<CallState>({
    phase: 'idle',
    bubbles: [],
    fields: [],
    session: null,
    error: null,
    connecting: false,
    ended: false,
  });

  /**
   * Close the line: the microphone and the room both.
   *
   * Safe to call twice — the room is cleared before it is disconnected, so the
   * Disconnected event this raises does not come back round for a second go.
   */
  const hangUp = useCallback(() => {
    if (drainRef.current !== null) {
      clearTimeout(drainRef.current);
      drainRef.current = null;
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
        // Nothing the patient says now is wanted, so the mic goes at once —
        // but the assistant may still be a word or two from being heard.
        mic.release();
        drainRef.current ??= setTimeout(hangUp, DRAIN_MS);
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
    setState((s) => ({ ...s, connecting: true, error: null }));
    try {
      // ── 1 · the microphone, before anything else ──
      // The assistant starts talking the moment a session exists, so asking
      // for the mic first means no patient is ever greeted by an assistant
      // that cannot hear them, and no orphan session is created if they say no.
      const micTrack = await mic.open();

      // ── 2 · the session ──
      const res = await fetch('/api/session', { method: 'POST' });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? 'could not start');
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
      }));
    }
  }, [apply, hangUp, mic]);

  const sendTyped = useCallback(
    async (text: string) => {
      const id = state.session?.sessionId;
      if (!id || !text.trim()) return;
      await fetch(`/api/session/${id}/typed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
    },
    [state.session?.sessionId],
  );

  return { ...state, start, sendTyped };
}
