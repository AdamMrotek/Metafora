# metafora.care

Clinical voice intake. The pieces and how they talk to each other are
[`docs/system-map.md`](./docs/system-map.md); this page is how to run what
exists today.

| Piece | Folder | Status |
|---|---|---|
| `app-call` — the patient's screen | `frontend/call` | **built** |
| `app-dashboard` — clinician screens | `frontend/dashboard` | spec only |
| `app-studio` — authoring and evals | `frontend/studio` | spec only |
| `svc-core` — permissions and every request | `services/core` | HTTP surface only |
| `svc-agent` — the conversation | `services/agent` | **built** |
| `svc-media` — connection and audio | `services/media` | **built** |
| `svc-comms` — anything leaving the perimeter | `services/comms` | not started |

What is built is phase 1 of
[`docs/self-hosted-voice-architecture.md`](./docs/self-hosted-voice-architecture.md):
one `livekit-server`, one backend process that is *a participant in the call*
rather than a service beside it, and Groq behind three swappable interfaces.

LiveKit is used strictly as a WebRTC transport. The audio loop, the state
machine, the safety gate and the session log are all ours.

**Three services, one process.** `svc-core` imports `svc-agent` and
`svc-media`, so today all three run as one. Folders are not deployments — the
split exists so that stops being true without a rewrite.

## Running it

You need a `GROQ_API_KEY` and the LiveKit SFU.

```bash
brew install livekit          # one Go binary, no dependencies
npm install
cp .env.example .env          # then put your GROQ_API_KEY in it
```

Three terminals:

```bash
npm run livekit               # SFU on :7880 (dev mode: devkey / secret)
npm run dev:core              # our participant + HTTP, on :3000
npm run dev:call              # patient portal, on :5173
```

Open <http://localhost:5173> and tap **Start the conversation** once. That is
the only tap in the interview.

```bash
npm test                      # 61 tests, no API key needed
npm run typecheck
npm run bench                 # measure real latency (needs GROQ_API_KEY)
```

> **Free-tier limit:** Orpheus TTS allows 10 requests/minute and 100/day. One
> interview costs roughly 8–15, so expect a handful of runs per day before a 429.

## How a turn works

```
patient speaks
  → LiveKit SFU  → svc-media, 32 ms frames @ 16 kHz     (media/src/room.ts)
  → Silero VAD + endpointing               (agent/src/config/tuning.ts)
  → Groq whisper-large-v3-turbo            (agent/src/inference/groq.ts)
  → ▶ SAFETY GATE, before generation       (agent/src/safety/scan.ts)
  → interview state machine + tool permission matrix
  → Groq gpt-oss-120b, streaming
  → sentence chunks ≤200 chars → Orpheus TTS @ 48 kHz
  → paced back into the published track
```

Step 4 is the point of the whole architecture. The transcript passes through
our code before it reaches the model, and the reply passes through our code
before it reaches the patient, so the gate cannot be bypassed and a tool call
physically cannot route around us.

## Where the code lives

```
frontend/   dashboard · call · studio · shared   what a browser loads
shared/     auth · contracts                     both sides use it
services/   core · agent · media · comms         what runs continuously
db/         clinical · transcript · config · metrics
docs/       anything spanning more than one piece
```

Every app and service folder holds its own `docs/`, starting with an
`overview.md` saying what the piece is and what of it exists yet. The frozen
UX specs live with the app they describe — `frontend/dashboard/docs/` and
`frontend/studio/docs/`.

## What is hardcoded, and where to unhardcode it

| Thing | File | Becomes |
|---|---|---|
| The interview | `services/agent/src/config/protocol.ts` | published by `app-studio` into `store-config` |
| The dispatch queue | `services/core/src/config/queue.ts` | a real queue over `store-clinical` |
| Endpointing timings | `services/agent/src/config/tuning.ts` | stays here, tuned against real speech |
| Conversational pace | `services/agent/src/config/tuning.ts` | `sentencePauseMs`, `questionPauseMs` |

The protocol object already has the shape `frontend/studio/docs/ux-agent-studio.html`
specifies — all six blocks, frozen at publish — so `app-studio` has somewhere to
publish into.

## Session logs

Written from inside the media path to `logs/<sessionId>.jsonl`, first-hand
rather than reconstructed from what the browser relayed. Both sides of the
conversation, every state transition, every safety decision, and per-turn
latency. This is `store-transcript` before it is a database.

```bash
jq -c 'select(.type=="latency.turn")' logs/*.jsonl
jq -c 'select(.type=="safety.scanned")' logs/*.jsonl
```

Raw audio is not retained — transcripts and state transitions only.

## Notes on the architecture document

`docs/self-hosted-voice-architecture.md` predates this implementation and is
stale on two of its three inference stages. See
[`services/agent/docs/inference-corrections.md`](./services/agent/docs/inference-corrections.md).
