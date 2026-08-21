/**
 * `svc-agent` — the conversation.
 *
 * Fetches the interview definition, speaks with the patient through
 * `svc-media`, decides what to ask next, and writes the transcript itself as
 * the call happens. This file is the surface other services may reach for;
 * everything else in `src/` is internal to the loop.
 */
export { PROTOCOLS } from './config/protocol.ts';
export { GroqProvider } from './inference/groq.ts';
export { createSession, endSession, getSession, type Session } from './session/store.ts';
export { ConversationLoop } from './voice/loop.ts';
