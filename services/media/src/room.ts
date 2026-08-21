import {
  AudioFrame,
  AudioSource,
  AudioStream,
  LocalAudioTrack,
  RemoteAudioTrack,
  Room,
  RoomEvent,
  TrackPublishOptions,
  TrackSource,
} from '@livekit/rtc-node';
import {
  encodeMessage,
  FRAME_SIZE_MS,
  OUTPUT_SAMPLE_RATE,
  SAMPLE_RATE,
  type ServerMessage,
} from '@metafora/contracts';

/**
 * The backend as a participant.
 *
 * This process is *in* the call, not a service standing beside it. Everything
 * the patient says arrives here as frames before it is anything else, and
 * everything the assistant says leaves from here — which is what makes the
 * safety gate an inline gate and the session log a first-hand record.
 *
 * LiveKit is used strictly as a WebRTC transport. Nothing above this file
 * knows it exists.
 */

/** Frames pushed to the patient. 10 ms is small enough to stop promptly. */
const OUT_FRAME_SAMPLES = (OUTPUT_SAMPLE_RATE * 10) / 1000;

export class AssistantRoom {
  private readonly room = new Room();
  private source: AudioSource | null = null;
  private patientTrack: RemoteAudioTrack | null = null;

  private constructor(readonly roomName: string) {}

  /**
   * Join, publish, and wait until we are actually in the room.
   *
   * The caller mints the patient's token only after this resolves, so there is
   * no window in which a patient sits in an empty room. That guarantee is free
   * because one process does both jobs; splitting them later means engineering
   * it back.
   */
  static async join(opts: {
    url: string;
    token: string;
    roomName: string;
    onPatientJoined?: (identity: string) => void;
    onPatientLeft?: (identity: string) => void;
  }): Promise<AssistantRoom> {
    const self = new AssistantRoom(opts.roomName);

    self.room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track instanceof RemoteAudioTrack) self.patientTrack = track;
    });
    self.room.on(RoomEvent.ParticipantConnected, (p) => opts.onPatientJoined?.(p.identity));
    self.room.on(RoomEvent.ParticipantDisconnected, (p) => opts.onPatientLeft?.(p.identity));

    await self.room.connect(opts.url, opts.token, { autoSubscribe: true, dynacast: false });

    // Orpheus hands us 48 kHz directly, so the published track runs at the
    // room's native rate and nothing is resampled on the way out.
    self.source = new AudioSource(OUTPUT_SAMPLE_RATE, 1);
    const track = LocalAudioTrack.createAudioTrack('assistant', self.source);
    const options = new TrackPublishOptions({ source: TrackSource.SOURCE_MICROPHONE });
    await self.room.localParticipant!.publishTrack(track, options);

    return self;
  }

  /** Wait for the patient's microphone track, or give up. */
  async waitForPatientAudio(timeoutMs = 30_000): Promise<RemoteAudioTrack> {
    const deadline = Date.now() + timeoutMs;
    while (!this.patientTrack) {
      if (Date.now() > deadline) throw new Error('patient audio never arrived');
      await new Promise((r) => setTimeout(r, 100));
    }
    return this.patientTrack;
  }

  /**
   * Patient audio as 32 ms frames at 16 kHz.
   *
   * The SDK resamples 48 kHz → 16 kHz natively and hands back exactly the
   * frame size Silero requires, so there is no resampler and no repacking
   * buffer in this codebase.
   */
  async *patientFrames(track: RemoteAudioTrack): AsyncIterable<Int16Array> {
    const stream = new AudioStream(track, {
      sampleRate: SAMPLE_RATE,
      numChannels: 1,
      frameSizeMs: FRAME_SIZE_MS,
    });
    for await (const frame of stream) yield frame.data;
  }

  /**
   * Play PCM to the patient, paced.
   *
   * `captureFrame` blocks until the track's queue has room, so awaiting it is
   * the backpressure: TTS generates faster than realtime and this is what
   * stops it being dumped into the room in one burst.
   */
  async play(pcm: Int16Array, signal?: AbortSignal): Promise<void> {
    const source = this.source;
    if (!source) throw new Error('room has no published track');

    for (let at = 0; at < pcm.length; at += OUT_FRAME_SAMPLES) {
      if (signal?.aborted) return;
      const slice = pcm.subarray(at, Math.min(at + OUT_FRAME_SAMPLES, pcm.length));
      const frame = new AudioFrame(
        Int16Array.from(slice),
        OUTPUT_SAMPLE_RATE,
        1,
        slice.length,
      );
      await source.captureFrame(frame);
    }
  }

  /**
   * Deliberate silence, paced like speech.
   *
   * Pushed through the same track rather than implemented as a `setTimeout`,
   * so the queue accounting, the playout waiter and `clearQueue()` all treat
   * a pause exactly like audio — which means a patient can talk over a pause
   * and be heard, as they would with a real person drawing breath.
   */
  async playSilence(ms: number, signal?: AbortSignal): Promise<void> {
    const samples = Math.round((OUTPUT_SAMPLE_RATE * ms) / 1000);
    if (samples > 0) await this.play(new Int16Array(samples), signal);
  }

  /** Wait until everything queued has actually been heard. */
  async waitForPlayout(): Promise<void> {
    await this.source?.waitForPlayout();
  }

  /**
   * Barge-in: drop everything queued but not yet played.
   *
   * Without this the assistant keeps talking for as long as the buffer is
   * deep, which is precisely the moment a patient concludes it is not
   * listening to them.
   */
  interrupt(): void {
    this.source?.clearQueue();
  }

  /** Tell the browser what to draw. One transport, no polling. */
  send(message: ServerMessage): void {
    void this.room.localParticipant
      ?.publishData(encodeMessage(message), { reliable: true, topic: message.t })
      .catch((err) => console.error('[room] publishData failed', err));
  }

  async close(): Promise<void> {
    try {
      await this.source?.close();
    } finally {
      await this.room.disconnect();
    }
  }
}
