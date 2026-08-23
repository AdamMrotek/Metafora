import { useCallback, useEffect, useMemo, useRef } from 'react';

/**
 * The microphone, from open to released.
 *
 * The one promise this hook makes: no live track outlives it. A browser keeps
 * its recording indicator lit for as long as a track exists, and handing a
 * track to a WebRTC room does not hand over ownership of it — the raw
 * MediaStreamTrack stays ours to stop. A patient's mic left hot after the call
 * has ended is not a cosmetic bug, so releasing it is a lifecycle guarantee
 * rather than something a caller has to remember on every path.
 */
export function useMic() {
  const trackRef = useRef<MediaStreamTrack | null>(null);

  /** Stop the track. Idempotent — every teardown path may call it. */
  const release = useCallback(() => {
    trackRef.current?.stop();
    trackRef.current = null;
  }, []);

  // The guarantee. Whatever the caller forgets, unmounting closes the mic.
  useEffect(() => release, [release]);

  /**
   * Ask for the microphone, replacing any track already open.
   *
   * Throws with the sentence the patient should read: refusing the prompt is
   * the one failure here that a person can actually do something about.
   */
  const open = useCallback(async (): Promise<MediaStreamTrack> => {
    release();
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
      trackRef.current = track;
      return track;
    } catch {
      throw new Error(
        'This call needs your microphone. Allow access in your browser, then start the conversation again.',
      );
    }
  }, [release]);

  // Stable, because callers hang teardown effects off this handle: a fresh
  // object each render would re-run those effects and end the call mid-call.
  return useMemo(() => ({ open, release }), [open, release]);
}
