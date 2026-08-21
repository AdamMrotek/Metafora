/**
 * `svc-media` — the only thing a patient ever connects to.
 *
 * Transport only. It terminates the WebRTC connection, carries audio in both
 * directions, and lets an agent session attach to it. No clinical logic passes
 * through this package, and it never sees a transcript.
 */
export { AssistantRoom } from './room.ts';
