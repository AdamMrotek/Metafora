# `svc-agent` — code review, framework decision, and the Python backend

**What this is.** The findings of one review session: a file-by-file audit of the agent
flow as it stands, where its complexity actually sits, a local simplification plan, and the
evaluation that ended in choosing Pipecat. It closes with what that choice does to the
backend — one language, one service, two machines — and with the one guarantee worth
building deliberately rather than inheriting.

**Status:** migration complete, 23 August 2026 — see §11 for what it found, including the
predictions in §4 and §10 that turned out to be wrong. Review complete, 21 August 2026 ·
decision recorded and extended to the backend
and its deployment, 22 August 2026 · migration not started. The audit in §1–§3 describes the
code at commit `4bd1a4d` plus uncommitted working-tree changes. §4 onward is forward-looking.

---

## 1 · The flow as it stands

```
browser taps Start
  → core/index.ts       POST /session: create record, join room, mint token
  → media/room.ts       we are a participant; 32ms/16kHz frames in, 48kHz out
  → voice/vad.ts        Silero scores each frame → "turn is over"
  → inference/groq.ts   whisper → text
  → safety/scan.ts      ▶ gate, before generation
  → inference/groq.ts   gpt-oss ×2 (speech pass + capture pass), streaming
  → voice/reasoning.ts  strip <think>
  → voice/sentences.ts  cut into ≤200-char sentences
  → voice/speech-queue.ts  synthesise ahead, play in order
  → media/room.ts       paced back into the track
  → session/log.ts      every step, to logs/<id>.jsonl
```

| File | Lines | Job |
|---|---|---|
| `core/src/index.ts` | 154 | HTTP. `POST /session` joins the room *then* hands the patient a token, so they never arrive alone. Holds the live-call map, teardown, shutdown. |
| `media/src/room.ts` | 194 | The only file that knows LiveKit exists. Frames in, PCM out, `interrupt()`, `send()` for UI messages. |
| `agent/src/index.ts` | 12 | Public surface: 5 exports. |
| `voice/loop.ts` | **623** | The orchestrator. Everything below is called from here. |
| `voice/vad.ts` | 198 | Endpointing: pre-roll, speech onset, 700 ms silence → commit turn, barge-in detection. |
| `voice/sentences.ts` | 186 | Streaming text → speakable chunks. Handles "Dr.", "5.5 mg", the 200-char cap, packs later sentences to save TTS requests. |
| `voice/reasoning.ts` | 77 | Strips `<think>` from a *stream* (tags split across deltas). |
| `voice/speech-queue.ts` | 48 | Synthesise ahead, play strictly in order. |
| `voice/wav.ts` | 144 | WAV encode/decode, resample, trim silence. |
| `inference/types.ts` | 63 | The three-method provider interface. |
| `inference/groq.ts` | 223 | The only file naming a vendor. STT/LLM/TTS + SSE parsing + tool-call reassembly. |
| `safety/scan.ts` | 74 | Deterministic red-flag matcher. No model in it, by design. |
| `session/machine.ts` | 140 | Protocol → ordered question list, captured values, tool permission matrix, tool schema. |
| `session/store.ts` | 143 | The in-memory session record + **both system prompts**. |
| `session/log.ts` | 84 | Append-only JSONL, typed event union. |
| `config/tuning.ts` | 110 | Every timing constant and model id. |
| `config/protocol.ts` | 95 | The hardcoded interview. |

Roughly 2,366 lines of production code in `services/agent`, plus 194 in `svc-media` and
154 in `svc-core`. About 975 lines of tests.

---

## 2 · Where the complexity actually is

The ~650 lines in `vad.ts` / `sentences.ts` / `reasoning.ts` / `speech-queue.ts` /
`wav.ts` **are not the problem.** Each is one pure, tested class that exists because of a
specific bug — double-speed audio, reasoning read aloud to a patient, an audible seam
through "Dr. Hollis". This is the healthy part of the codebase.

The complexity is concentrated in `loop.ts` — 413 lines of actual code (24% comments)
doing eight jobs — and it radiates outward from one decision: **two LLM calls per turn.**

Because gpt-oss emits speech *or* a tool call but never both, a whole second completion
runs just to write the record. That single decision is why all of these exist:

`captureFields` · `capturePrompt` · `dispatchTool` · `ToolOutcome` ·
`machine.authorise` · `machine.toolDefinitions` · `ToolCall`/`ToolDefinition` types ·
tool-fragment reassembly in the SSE loop · `toWireMessage`'s tool branches ·
`role:'tool'` history entries · the tool-name stripping in `sanitise`

---

## 3 · Local simplification plan

The fallback if the migration stalls, ranked by complexity removed over risk. Items 2, 3 and
5 evaporate with `loop.ts` and are listed for completeness; **items 1 and 4 outlive the
migration** — item 1 as a question Python inherits, item 4 as a bug in code that must not be
ported by accident.

**1. Kill the second LLM pass.** ~180 lines and one entire concept. The machine already
knows which field is live and the patient's transcript *is* the answer — the prompt
already asks the model to record "their own words". `machine.capture(currentField,
transcript)` gets most of the way with zero model calls, and halves per-turn LLM cost and
rate-limit pressure.

*Costs:* less cleanup when a patient rambles or answers two questions at once; loses the
permission matrix, which the README sells as an architectural guarantee — though today it
guards exactly one tool writing a field the machine already tracks.

*Related fragility, worth fixing either way:* **only a successful tool call advances the
interview.** If the capture pass fails, the call stalls forever with no fallback.

**2. Split `loop.ts` in two.** `generate` + `queueFor` + `forSpeech` + `speakLine` +
`sanitise` + the `AbortController` are one coherent object — "say this, let me interrupt".
Pull them into a `Speaker`. `loop.ts` then reads as the six-step turn it claims to be, at
~250 lines.

**3. One phase setter.** `room.send({t:'phase'})` appears 9 times, alongside three
overlapping state flags (`speaking`, `turnRunning`, `closed`). A single `phase` field whose
setter publishes removes the drift between internal state and what the browser draws.

**4. One turn entry point — this one is a live bug.** `runTurn` and `submitTyped` differ
only in how text arrives, but `submitTyped` skips the `turnRunning` lock entirely. A typed
answer during a voice turn runs a **second concurrent turn** and interleaves into
`history`. Fold both into one guarded `takeTurn(text, meta)`. In Pipecat this is the
question of how typed input is injected into a live pipeline — same bug, new spelling, and
it must be answered rather than inherited.

**5. Delete confirmed dead code** (~60 lines, all verified unused outside tests):
`machine.recordFollowUp` · `TURN_TUNING` · the `packAfterFirst = false` branch ·
`Endpointer.reset()` · the `speech_start` and `discarded` VAD events (emitted, never
consumed) · `CompiledState.sectionTitle` · `utterance.reason`.

Net: ~2,366 production lines → ~2,050, `loop.ts` under 300, one fewer round trip per turn.

---

## 4 · Framework evaluation

### Pipecat

Server is **Python-only**; the TypeScript packages (`@pipecat-ai/client-js`) are
client-side. Ships an official **LiveKit transport**, so the SFU and `frontend/call` are
unaffected.

- **Flows** — as of `pipecat-ai` 1.5.0 folded into core under `pipecat.flows`, no separate
  package to version-match. `FlowManager` orchestrates `NodeConfig` states,
  `FlowsFunctionSchema` for functions, static *or* dynamic flows, per-provider function-call
  adapters, context strategies on transition (APPEND / RESET / RESET_WITH_SUMMARY). The docs
  name "intake forms" as the static-flow use case.
- **Evals** — `pipecat eval run scenario.yaml`. YAML turns + semantic assertions, LLM judge
  (local Ollama by default). Text mode (skips audio, fast) and audio mode (real TTS→STT).
  Asserts on response content, multi-turn context, function calls, interruptions, **latency
  budgets**, vision.
- **Observability** — built-in OTel, conversation→turn→service span hierarchy, TTFB /
  processing / usage metrics, **Whisker** live pipeline debugger, Tail terminal dashboard,
  Sentry, Observer pattern for custom instrumentation.
- **Model switching** — `LLMSwitcher`, explicit, context preserved, composes with Flows.
  Constraint: all LLMs in the switcher must support the same function set (irrelevant here,
  one tool).

### LiveKit Agents

`@livekit/agents` **1.7.0**, published 2026-08-20, Apache-2.0. Python SDK is the flagship;
JS is a port.

- **Hooks** — `onUserTurnCompleted(ctx, chatCtx, newMessage)` with `StopResponse` is a
  precise slot for the safety gate. `sttNode` / `llmNode` / `ttsNode` overrides for custom
  providers, in both languages.
- **Evals** — `livekit.agents.evals`: `JudgeGroup`, built-in `AccuracyJudge` (grounding —
  is the response supported by function-call output?) and `TaskCompletionJudge` (modelled on
  First Call Resolution). Code-based, via pytest **or Vitest**.
- **Observability** — LLMMetrics (TTFT, tok/s, tokens), STTMetrics, TTSMetrics, VADMetrics,
  **EOUMetrics**; OTLP export anywhere; Prometheus.
- **Tools / structure** — agent handoffs: a `@function_tool` returns the next `Agent`, the
  session swaps in place with full context. Per-agent LLM/STT/TTS overrides.
- **Model switching** — `Agent.update_options()`, or the handoff itself. Open issue #4691:
  handing off from a realtime model back to a text LLM.
- **JS-specific gaps** — no `@livekit/agents-plugin-groq` on npm (404; Python-only). Groq
  TTS through the OpenAI plugin fails on `response_format` — [agents-js
  #650](https://github.com/livekit/agents-js/issues/650), closed as *not planned*.

### What neither one gives us

- **The session log.** Every safety decision and state transition, recorded first-hand from
  inside the media path. OTel is telemetry, not the same artefact. The typed event union in
  `log.ts` survives in every scenario; only its sink changes (§7). Do not let a metrics
  story talk us out of it.
- **The two-pass problem.** gpt-oss emitting speech XOR tool call is a model property. The
  real fix is a model that does both (`llama-3.3-70b-versatile`) or deterministic capture —
  test it independently, and **do not bundle it with a migration.**
- **Orpheus TTS quirks.** The 200-char cap, the lying `sample_rate` header, the silence
  padding. `wav.ts` and `sentences.ts` as files do not survive, but everything they know
  does, and it has to be re-encoded in whatever Python TTS service wraps Orpheus. Read
  their comments before deleting them.

---

## 5 · Decision

**Pipecat**, over LiveKit transport.

The deciding argument is `app-studio`. Protocols authored as *data* by non-engineers is a
much shorter distance to a Flows `NodeConfig` graph than to LiveKit agent handoffs, and
YAML eval scenarios are reviewable by a clinician in a way a Vitest file never will be.
Flows also makes tool availability a property of the node — the model never *sees* a tool
it cannot call, which is structurally better than authorising post-hoc the way
`machine.authorise` does today.

Accepted costs: Pipecat's pipeline abstraction stacked over LiveKit's transport, and a
rewrite of everything in §1 that is not pure logic.

---

## 6 · The backend becomes Python — all of it

An earlier draft of this section split `services/core` down the middle: session lifecycle to
Python, permissions and stores staying TypeScript. **That is reversed.** The backend is one
Python service.

The split rested on `shared/auth` being shared *code* with `app-dashboard` and `app-studio`
— role evaluation written once, imported by three TypeScript consumers. But `shared/auth`
has no code in it, and neither dashboard nor studio exists. The cost of losing that sharing
is entirely prospective, and it is paid against a benefit that is immediate and daily: one
runtime on the box, one dependency manager, one lockfile, one test command, one deploy.

What does *not* change is that `core/src/index.ts` today is the agent's host process wearing
core's name. It calls `new ConversationLoop(...)`, holds a `live` map, routes
`POST /session/:id/typed` into `loop.submitTyped()`. Every one of those in-process calls
breaks the moment the agent becomes a Pipecat bot — and every one of them is trivially
re-expressible when the thing on the other side is also Python.

### Target layout

The `services/` folders keep their meaning. They stop being separate npm workspaces and
become packages inside one Python project.

```
pyproject.toml           one dependency set, one venv, uv.lock
services/
  agent/                 Pipecat pipeline, Flows, safety gate, session log,
                         protocol execution
  core/                  session lifecycle HTTP, permissions, store access,
                         dispatch queue, dashboard + studio APIs
  media/                 deleted — Pipecat's LiveKit transport replaces room.ts
  comms/                 unstarted
frontend/                TypeScript, deployed separately
shared/contracts/        pydantic models + generated TS (§9)
```

Stack: Python 3.12, `uv`, FastAPI + uvicorn, `pipecat-ai` with its LiveKit / Groq / Silero
extras, `livekit-api` for token minting, SQLAlchemy async + Alembic against Postgres,
pydantic for contracts, `import-linter` for §8.

`svc-core` still gets *smaller* than it is now, which will feel wrong and is correct — it
finally becomes only the thing it was specced to be. It just stays in the same process as
the thing it stopped hosting.

---

## 7 · Deployment

Two machines and a database we do not run. The frontend is not on either machine; it builds
and deploys separately to a static host.

```
Machine 1 — LiveKit
  livekit-server
  public: wss://live.<domain>          browsers connect here directly
  ports: 443 TCP (wss + TURN over TLS), 7881 TCP, UDP 50000–60000

Machine 2 — the backend
  uvicorn :3000                        FastAPI + Pipecat, one process
  Caddy :443                           TLS termination only, no static files
  public: https://api.<domain>
  outbound: LiveKit, Postgres, Groq. Nothing else inbound.

Managed Postgres                       store-clinical, store-config, store-transcript
```

LiveKit is separate because it is the only piece with awkward networking — a wide UDP range,
and TURN over TLS:443 as the *assumed* path rather than the fallback, since clinic networks
block outbound UDP. Keeping it on its own box leaves Machine 2 with one open port.

Deploy is `git pull && uv sync && systemctl restart metafora`. There is no Dockerfile, no
compose file and no CI in the repo today; none of that is a prerequisite for this shape.

**Two things that need deciding rather than defaulting:**

**Where the bot runs.** Pipecat's dev runner spawns a subprocess per call. That breaks "one
service", so instead `POST /session` starts the pipeline as an asyncio task in the same
process — Pipecat is asyncio-native and this works directly. The cost, stated plainly: a
crash in one bot can take the API with it. Subprocess or worker-pool isolation is the
production answer and is the same split the architecture doc reaches for when audio capacity
runs out, past a few dozen concurrent conversations. One box until then.

**Drain on deploy.** This process holds live WebRTC connections. Restarting it drops every
call in progress, so shutdown has to stop accepting new sessions and wait for active ones to
finish. This is already true of the Node process today and is already unimplemented. It is
the single item that separates "runs on a server" from "deployable".

---

## 8 · The write boundary: one patient per call

The agent writes two things to the database — the session log and the interview summary —
and it must not be able to write either against any patient except the one it is talking to.

Everything runs in one process, so this cannot be a network rule. It is a code boundary, and
it is worth building deliberately because it is the guarantee `db/clinical/README.md` already
claims ("written through `svc-core` and nothing else") and the one `machine.authorise`
gestures at today.

**Give the bot a handle that has no way to name a different patient.**

```python
# services/core/contracts.py — the whole of what the bot knows about storage
class SessionWriter(Protocol):
    async def append_log(self, event: LogEvent) -> None: ...
    async def save_summary(self, summary: IntakeSummary) -> None: ...
```

No patient id parameter anywhere. `services/core` constructs one of these per session with
the id closed over, and hands it to the pipeline; the bot holds it for the length of the call
and cannot reach past it.

The TypeScript version of this would have been enforced for free — `services/agent` simply
would not list the store in its `package.json`. Python has no equivalent, so the boundary is
declared explicitly and checked in CI:

```ini
# .importlinter
[importlinter:contract:agent-cannot-reach-the-store]
name = agent must not import the store
type = forbidden
source_modules = services.agent
forbidden_modules = services.core.store
```

Same guarantee, one dev dependency, and the reason for it is worth a sentence in the README:
we noticed the language would not enforce it and did not assume it away.

**What production would add**, and we will not build: a separate Postgres role plus row-level
security, so the constraint lives in the database rather than only in our code. Knowing the
difference is the point; implementing it is not, at this stage.

---

## 9 · `shared/contracts` — pydantic becomes the source of truth

206 lines. The direction of generation reverses now that no backend consumer is TypeScript.

| Part | Lines | Disposition |
|---|---|---|
| `audio.ts` | 16 | **Deleted.** Frame sizes for Silero; Pipecat owns this now. Nothing outside the pipeline needs it. |
| `wire.ts` | 39 | **Generated.** 4 `ServerMessage` shapes, `ClientMessage`, `SessionBootstrap`. The earlier plan was a hand-written pydantic mirror with a parity test; with pydantic as the source that test disappears and so does the mirror. |
| `types.ts` | 148 | **Generated.** `ProtocolVersion`, `Question`, `RedFlag`, `ToolSpec`. `app-studio` authors these values, the bot executes them — but the *shape* is defined once, in Python. |

One pipeline: pydantic → JSON Schema → TypeScript, committed, with CI failing if the
committed output is stale. No hand-mirroring anywhere, and the churn in `types.ts` — which
§4 correctly predicted would be the worst of it — costs one regeneration.

---

## 10 · Practical notes and open questions

- **The frontend stops sharing an origin.** `frontend/call/vite.config.ts:10` proxies `/api`
  to `localhost:3000`; there is no proxy in production once the frontend deploys separately.
  It needs the backend's absolute URL as a build-time variable, and `services/core` needs to
  name the frontend's origin in CORS rather than allowing everything. Small, but currently
  implicit in both directions.
- **Endpointing is a stated clinical position.** `tuning.ts` argues the 700 ms threshold is
  ours to own and explain rather than inherited from a framework tuned for consumer voice
  agents. Whatever Pipecat's turn detection defaults to, that argument still needs an
  answer — either we keep our own bounds around it, or we write down why the framework's is
  better for a patient who trails off mid-sentence. This is the one place where accepting a
  default silently would undo something the codebase currently does well.
- **The session log's sink changes, its contents do not.** `logs/<id>.jsonl` becomes rows in
  `store-transcript`, written through the `SessionWriter` of §8. The typed event union ports
  as-is. Losing the append-only-file property is a real trade and should be answered by
  Postgres-side constraints, not by hoping.
- **Verify Pipecat's sentence aggregation can enforce a hard character cap** before deleting
  `sentences.ts`. Orpheus's 200-char limit is not a preference. If the aggregator cannot, that
  logic ports rather than dies.
- **Test the two-pass question separately**, before or after the migration, never during.
- **61 existing tests** cover the pure units. `scan` and `machine` port to pytest — they are
  the two files whose logic is the product rather than the plumbing. `vad`, `sentences`,
  `reasoning`, `speech-queue`, `wav` and `loop` retire with their implementations. Do not
  count that as lost coverage; count it as coverage that moved into Pipecat's.

---

## 11 · What the migration actually found

Written after the fact, 23 August 2026. The decision held; several of the details did not.
Recorded because the wrong predictions are more useful than the right ones.

**Pipecat is 1.7.0, not 1.5.0.** `PipelineTask` is a deprecated alias for `PipelineWorker`,
driven by `WorkerRunner`. `PipelineWorker(app_resources=...)` is the injection point for the
`SessionWriter` of §8 — cleaner than this document guessed — and `worker.stop_when_done()`
makes the drain in §7 a small change rather than a project.

**§10's endpointing worry was right, but about the wrong mechanism.** The concern was that
Pipecat's default `stop_secs` (0.2 s) would silently replace our 0.7 s. In 1.7 the VAD does
not end turns at all: the default user-turn *stop* strategy is `LocalSmartTurnAnalyzerV3`, a
model that judges whether the speaker finished a thought. Setting `stop_secs` would have
looked like it worked and changed almost nothing.

We are running the framework's turn detection and **measuring** rather than asserting: every
turn writes an `endpoint.decision` event with the silence the patient actually sat through and
what the 700 ms floor would have done. Early observations put smart turn between 700 ms and
1270 ms — sometimes at the floor, sometimes at nearly twice it, and it is consistently the
largest single component of perceived latency. If we want the floor back it is
`SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.7)`, whose own docs call it a
"policy floor". That decision now has data behind it instead of an argument.

**The two-pass question answered itself.** §3 proposed killing the second LLM pass and §4 said
to test it separately. An intermediate single-pass build was written and it is *broken*, in a
way worth recording: given the conversational prompt, a patient's answer and the
`update_intake` schema, gpt-oss returned a tool call and empty `content` in **5 trials out of
5**. A single pass holding the schema goes silent on exactly the turn the patient just
answered. The XOR property is real, current, and not a prompt problem. Two passes run
concurrently in a `ParallelPipeline`, which is a documented Pipecat pattern rather than a
fight with the framework.

**`sentences.ts` and half of `wav.ts` survive, as §10 suspected.** No built-in aggregator
enforces a hard character cap, so Orpheus's 200-char limit ports as a custom
`BaseTextAggregator` — which also absorbs the abbreviation rules, the request packing and the
`<think>` stripping. And Pipecat does *not* trim Orpheus's silence padding: measured on the
live API, `"How is your day going?"` came back as 1.10 s of speech inside 342 ms of leading
and 882 ms of trailing silence. Across three sentences, **44% of the audio was silence**.
`trimSilence` ports.

**`tuning.ts` was right about the sample rate, and Pipecat is safe by omission.** Re-verified:
24000, 48000 and the parameter omitted entirely all return byte-identical PCM; only the header
changes. `GroqTTSService` sends no `sample_rate`, so the header comes back honest at 24 kHz
and `base_output` resamples. Do not "fix" it by passing the room's rate.

**Two bugs the migration created and the record caught.** Both are the same mistake — an
observer sees *every* processor-to-processor hop, and both branches of a `ParallelPipeline`:

- Spoken text was appended once per link it crossed, repeating whole sentences into the
  patient's transcript. Frames are now counted by id, once.
- The capture pass's discarded prose ("✅ Recorded.") landed in the transcript as if it had
  been spoken. Frames are now attributed to the pass that produced them.

A third was structural: putting the assistant context aggregator *inside* the speech branch
made it swallow the reply, so the TTS never saw a word and the patient heard only the opening.
It belongs at the end of the pipeline, after the audio has been published.

**The typed path was a hole in the gate.** `POST /session/:id/typed` first injected an
`LLMMessagesAppendFrame`, which enters the context directly and never passes `SafetyGate` —
so a patient could *type* the one thing the gate exists to catch. Typed turns now enter as a
`TranscriptionFrame` and take the identical path: gated, logged, endpointed. §4's warning that
"same bug, new spelling" would need answering rather than inheriting was exact.

**A safety closure has to drain, not cancel.** Pushing `EndWorkerFrame` the moment a red flag
fired tore the pipeline down before the TTS had synthesised a word: the gate had worked, the
call had ended, and the patient was left in silence with no way to tell. The gate now speaks
first and asks `services.core` to `stop_when_done()`.

**What was cheaper than expected.** `scan.py`, `machine.py`, the prompts and the log event
union ported almost line for line. `frontend/call` needed *no* changes at all — confirmed by
`tsc` against the generated contracts — because `LiveKitTransport.send_message()` publishes
arbitrary JSON and the client ignores the LiveKit topic. RTVI had to be turned off
(`enable_rtvi=False`); left on it publishes hundreds of its own messages onto the same data
channel the browser is reading.

---

## Sources

[Pipecat Evals](https://docs.pipecat.ai/pipecat/evals/overview) ·
[Pipecat Flows](https://docs.pipecat.ai/pipecat-flows/introduction) ·
[LLMSwitcher](https://docs.pipecat.ai/api-reference/server/utilities/service-switchers/llm-switcher) ·
[Pipecat LiveKit transport](https://docs.pipecat.ai/api-reference/server/services/transport/livekit) ·
[LiveKit evals](https://docs.livekit.io/reference/python/livekit/agents/evals/index.html) ·
[LiveKit testing](https://docs.livekit.io/agents/start/testing/) ·
[Pipeline nodes and hooks](https://docs.livekit.io/agents/logic/nodes/) ·
[Agents and handoffs](https://docs.livekit.io/agents/logic/agents-handoffs/) ·
[LiveKit telemetry](https://deepwiki.com/livekit/agents/7.2-telemetry-and-observability) ·
[agents-js](https://github.com/livekit/agents-js) ·
[import-linter](https://import-linter.readthedocs.io/) ·
[uv](https://docs.astral.sh/uv/)
