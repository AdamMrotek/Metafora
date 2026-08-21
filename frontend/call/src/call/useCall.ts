import type {
  CallPhase,
  FieldState,
  ServerMessage,
  SessionBootstrap,
} from '@metafora/contracts';
import { decodeMessage } from '@metafora/contracts';
import { RoomEvent, Room, Track } from 'livekit-client';
import { useCallback, useRef, useState } from 'react';

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
  const roomRef = useRef<Room | null>(null);
  const [state, setState] = useState<CallState>({
    phase: 'idle',
    bubbles: [],
    fields: [],
    session: null,
    error: null,
    connecting: false,
    ended: false,
  });

  const apply = useCallback((message: ServerMessage) => {
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
  }, []);

  const start = useCallback(async () => {
    setState((s) => ({ ...s, connecting: true, error: null }));
    try {
      // ── 1 · the microphone, before anything else ──
      // The assistant starts talking the moment a session exists, so asking
      // for the mic first means no patient is ever greeted by an assistant
      // that cannot hear them, and no orphan session is created if they say no.
      let mic: MediaStreamTrack;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          // The assistant's voice comes out of the speaker and back into the
          // mic. Browser AEC is what stops it interrupting itself, and it
          // works because assistant audio arrives as a WebRTC track rather
          // than a separate element the canceller cannot see.
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        const [track] = stream.getAudioTracks();
        if (!track) throw new Error('no audio track');
        mic = track;
      } catch {
        throw new Error(
          'This call needs your microphone. Allow access in your browser, then start the conversation again.',
        );
      }

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
      room.on(RoomEvent.Disconnected, () =>
        setState((s) => ({ ...s, phase: s.ended ? 'ended' : 'idle' })),
      );

      await room.connect(url, token);
      // The line opens once and stays open. There is no push-to-talk here.
      await room.localParticipant.publishTrack(mic, { source: Track.Source.Microphone });

      setState((s) => ({
        ...s,
        session,
        fields: session.fields,
        connecting: false,
        phase: 'listening',
      }));
    } catch (err) {
      setState((s) => ({
        ...s,
        connecting: false,
        error: err instanceof Error ? err.message : 'could not start the call',
      }));
    }
  }, [apply]);

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
