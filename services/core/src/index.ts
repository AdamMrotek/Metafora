import cors from 'cors';
import express from 'express';
import { AccessToken } from 'livekit-server-sdk';
import type { SessionBootstrap } from '@metafora/contracts';
import {
  ConversationLoop,
  createSession,
  endSession,
  getSession,
  GroqProvider,
  PROTOCOLS,
} from '@metafora/agent';
import { AssistantRoom } from '@metafora/media';
import { nextInterview } from './config/queue.ts';

const PORT = Number(process.env.PORT ?? 3000);
const LIVEKIT_URL = process.env.LIVEKIT_URL ?? 'ws://localhost:7880';
const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY ?? 'devkey';
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET ?? 'secret';

const app = express();
app.use(cors());
app.use(express.json());

const ai = new GroqProvider(process.env.GROQ_API_KEY ?? '');
const live = new Map<string, { room: AssistantRoom; loop: ConversationLoop }>();

app.get('/health', (_req, res) => {
  res.json({ ok: true, live: live.size, livekit: LIVEKIT_URL });
});

/**
 * Start an interview.
 *
 * Four things, and the order is the point:
 *   1. create the session record and a room name
 *   2. join that room from this process and wait until we are actually in it
 *   3. mint a token scoped to that room and that patient
 *   4. return the token
 *
 * The browser connects and finds the assistant already there. Because we join
 * before we hand out the token there is no race and no window in which a
 * patient sits alone in an empty room.
 */
app.post('/session', async (_req, res) => {
  try {
    const interview = nextInterview();
    const protocol = PROTOCOLS[interview.protocolId];
    if (!protocol) throw new Error(`unknown protocol ${interview.protocolId}`);

    // 1 ─ the record
    const session = createSession(interview, protocol);

    // 2 ─ join, and wait
    const room = await AssistantRoom.join({
      url: LIVEKIT_URL,
      token: await mintToken(session.roomName, 'assistant', { canPublish: true }),
      roomName: session.roomName,
      onPatientJoined: (identity) => session.log.append({ type: 'patient.joined', identity }),
      onPatientLeft: () => void teardown(session.id, 'patient_left'),
    });
    session.log.append({ type: 'room.joined', identity: 'assistant' });

    const loop = new ConversationLoop(session, room, ai);
    live.set(session.id, { room, loop });

    // The loop runs for the life of the call; the request does not wait on it.
    void loop.start().catch((err) => {
      session.log.append({ type: 'error', where: 'loop', message: String(err) });
      console.error('[session] loop failed', err);
    });

    // 3 ─ the patient's token, scoped to this room and this identity
    const token = await mintToken(session.roomName, `patient-${interview.patient.id}`, {
      canPublish: true,
    });

    // 4 ─ hand it over
    const bootstrap: SessionBootstrap = {
      sessionId: session.id,
      roomName: session.roomName,
      clinician: protocol.clinician,
      patientFirstName: interview.patient.firstName,
      fields: session.machine.fields(),
    };
    res.json({ token, url: LIVEKIT_URL, session: bootstrap });
  } catch (err) {
    console.error('[session] failed to start', err);
    res.status(500).json({ error: err instanceof Error ? err.message : 'failed to start session' });
  }
});

/** The patient chose to type instead of speak. Same path, same record. */
app.post('/session/:id/typed', async (req, res) => {
  const entry = live.get(req.params.id);
  if (!entry) return res.status(404).json({ error: 'no such session' });

  const text = typeof req.body?.text === 'string' ? req.body.text : '';
  await entry.loop.submitTyped(text);
  res.json({ ok: true });
});

app.post('/session/:id/end', async (req, res) => {
  await teardown(req.params.id, 'ended_by_patient');
  res.json({ ok: true });
});

async function teardown(sessionId: string, reason: string) {
  const entry = live.get(sessionId);
  if (!entry) return;
  live.delete(sessionId);

  const session = getSession(sessionId);
  if (session) endSession(session, reason);

  await entry.loop.stop().catch(() => {});
  await entry.room.close().catch(() => {});
}

function mintToken(room: string, identity: string, grants: { canPublish: boolean }) {
  const token = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity,
    ttl: '2h',
  });
  token.addGrant({
    room,
    roomJoin: true,
    canPublish: grants.canPublish,
    canSubscribe: true,
    canPublishData: true,
  });
  return token.toJwt();
}

/**
 * This process is stateful: it holds live WebRTC connections, so restarting it
 * drops every call in progress. Draining properly is phase 2; closing the
 * rooms deliberately is the least we can do so a patient gets a disconnect
 * rather than silence.
 */
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, async () => {
    console.log(`\n[server] ${signal} — closing ${live.size} live call(s)`);
    await Promise.all([...live.keys()].map((id) => teardown(id, 'server_shutdown')));
    process.exit(0);
  });
}

app.listen(PORT, () => {
  console.log(`[server] http://localhost:${PORT}  ·  livekit ${LIVEKIT_URL}`);
  if (!process.env.GROQ_API_KEY) {
    console.warn('[server] GROQ_API_KEY is not set — calls will fail at the first turn');
  }
});
