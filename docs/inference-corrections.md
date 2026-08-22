# Corrections to the architecture note

Against [`docs/self-hosted-voice-architecture.md`](../../../docs/self-hosted-voice-architecture.md).
File paths below are relative to `services/agent/src/`.

Verified against Groq's live documentation on 2026-08-21, while implementing
phase 1. The *architecture* is unaffected — which is precisely the payoff of
the document's insistence on provider-shaped interfaces. Only `inference/groq.ts`
changed.

## The inference table is stale on two of three stages

| Stage | Document says | Actually available | What it cost us |
|---|---|---|---|
| STT | `whisper-large-v3-turbo` | ✅ still current | Takes audio *files*, not raw PCM. `voice/wav.ts` prepends a 44-byte header. |
| LLM | `llama-3.3-70b-versatile` | ❌ gone from production. Now `openai/gpt-oss-120b` / `-20b` | See below — this one has teeth. |
| TTS | `playai-tts` | ❌ gone. Now `canopylabs/orpheus-v1-english` | 200-character input cap; `sample_rate` up to 48000. |

### The LLM swap is not cosmetic

gpt-oss is a **reasoning** model. Left at defaults its chain of thought arrives
in `message.content`, and in this product `message.content` is *read aloud to a
patient*. Two parameters are load-bearing:

- `reasoning_format: 'hidden'` — keeps reasoning out of the reply entirely.
  Note that `raw` returns a 400 when tool calling is enabled, so the failure
  mode is at least loud rather than silent.
- `reasoning_effort: 'low'` — a voice turn cannot afford deliberation.

`voice/loop.ts` also strips `<think>` blocks before anything reaches TTS. That
is belt-and-braces on a clinical failure, not defensive programming for its own
sake.

### The TTS swap improved the design

Orpheus caps input at 200 characters, which forces the sentence-boundary
chunking the document already wanted for latency reasons. It also accepts
`sample_rate: 48000`, so audio comes back at the room's native rate and
**nothing is resampled on the output path**.

## Three of the six build-items were already in the SDK

The document lists six things we would have to build by declining the Agents
framework. Against `@livekit/rtc-node@0.13.34`, three of them are not work:

- **Item 6, resampling.** `new AudioStream(track, { sampleRate: 16000, frameSizeMs: 32 })`
  resamples 48 → 16 kHz natively, and 32 ms at 16 kHz is *exactly* the 512
  samples Silero requires. There is no resampler and no repacking buffer in
  this codebase.
- **Item 5, backpressure.** `AudioSource.captureFrame()` awaits an FFI callback
  that blocks until the track queue has room. Awaiting it *is* the pacing.
- **Item 2, barge-in flush.** `clearQueue()` drops queued frames and releases
  the playout waiter synchronously.

What remained genuinely ours is **endpointing** and **barge-in policy** — which
is what the document correctly identified as the hard part, and as clinical
behaviour we need to own under DCB0129.

## One interface signature had to widen

The document specifies:

```ts
complete(messages) → AsyncIterable<string>
```

That cannot carry a tool call, and the tool call is how the record gets
written. Ours yields a two-member event union instead:

```ts
complete(messages, opts) → AsyncIterable<{ type: 'text'; delta: string }
                                        | { type: 'tool'; call: ToolCall }>
```

The seam is unchanged; only the element type is wider.

## Still open

The document's open questions 1–5 are untouched by this implementation.
Question 3 in particular — what happens when the backend restarts mid-call —
is answered here only in the weakest way: `SIGINT`/`SIGTERM` close the rooms
deliberately so a patient gets a disconnect rather than silence. That is not
draining, and it is not a clinical decision yet.

---

# Measured, 2026-08-21

Run it yourself with `npm run bench` (5 iterations, real provider, real models,
same code the call runs).

## Perceived latency

| Stage | Median |
|---|---|
| STT (`whisper-large-v3-turbo`) | 194 ms |
| Safety gate | <1 ms |
| LLM first token (`gpt-oss-120b`, no tools) | 337 ms |
| TTS first audio (Orpheus, first sentence) | 382 ms |
| **Pipeline total** | **954 ms** |
| Endpoint wait (`TUNING.endpointSilenceFrames`) | 704 ms |
| Network + jitter (not measured) | ~100 ms |
| **Patient stops talking → hears a voice** | **~1.75 s** |

The document estimated 700–900 ms for the pipeline. 954 ms is close, and the
gate really is free. But the number a patient experiences is ~1.75 s, because
**the endpoint wait is on their clock too**. It is the largest single
contributor and it is a configuration value, not a cost — see
`config/tuning.ts`.

## gpt-oss will not speak and call a tool in the same turn

Measured 0/5 with a plain prompt and 0/5 with an explicit instruction to do
both. It is a property of the harmony format, not of the prompt.

Running the passes in sequence — speak, discover there was no speech, then run
a second completion — cost **1371 ms** of pipeline. The loop now runs them
**concurrently**: a speech pass carrying no tools (so it always speaks, and
streams immediately) alongside a silent capture pass that writes the record
while the audio is still playing. Only the speech pass is on the patient's
clock. That is the 954 ms above, a 417 ms saving.

## There is no working TTS rate control

Groq's Orpheus accepts a `speed` parameter, type-checks it (a string returns
400) and rejects unknown fields — so it is a real, recognised parameter — and
then **ignores it**. `speed: 0.6`, `1.0` and `1.8` return byte-identical
audio. A silent no-op is worse than an error, because you would ship believing
you had a rate control.

Its `[slowly]` vocal-direction cue is worse than useless: it produced *shorter*
audio (1.48 s) than no cue at all (1.60 s).

Punctuation does work — an ellipsis stretched the same sentence from 1.60 s to
2.00 s — but rewriting a clinician-facing sentence to slow it down is not a
trade worth making, not least because the same text is rendered in the thread.

What we use instead is `TUNING.sentencePauseMs` / `questionPauseMs`: real
silence pushed through the published track between chunks. Deterministic, no
quality loss, no words touched, and because it goes through the same track it
can be talked over exactly like speech.

## Rate limits shape the chunking strategy

Orpheus on the free tier: **10 requests/minute, 100 requests/day**. The limit
is on *requests*, not characters, and sentence-level chunking makes one request
per sentence — so the greeting alone cost 5. We hit a 429 mid-call during
testing.

The chunker now emits the **first** sentence alone (it is the only chunk on the
latency path) and packs everything after it up to the 200-character cap. The
greeting went from 5 requests to 2 with no change to first sound. Budget
roughly 8–15 requests per interview rather than 30–40, and expect the free tier
to allow only a handful of interviews per day.
