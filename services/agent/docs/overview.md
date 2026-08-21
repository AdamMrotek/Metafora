# `svc-agent` — the conversation

Runs one interview. Fetches the definition, speaks with the patient, decides
what to ask next, and **writes the transcript itself**, turn by turn, as the
call happens.

**What runs today**, all under `src/`:

| Folder | What it is |
|---|---|
| `voice/` | the loop: endpointing (`vad.ts`), turn commit, sentence chunking for TTS, reasoning-token filtering |
| `inference/` | the three model calls behind one declared surface — STT, LLM, TTS. `groq.ts` is today's provider, `types.ts` is the surface |
| `safety/` | the inline gate every generated utterance passes before it is spoken |
| `session/` | the interview state machine, the session record, and the transcript writer (`log.ts` → `logs/*.jsonl`) |
| `config/` | `tuning.ts`, the endpointing and barge-in constants; `protocol.ts`, today's hardcoded interview definition |

`vendor/silero_vad.onnx` is the VAD model, loaded by `voice/vad.ts`.

**The single declared surface** in `inference/types.ts` is what makes the
hosting question cheap later: moving a model in-house, out, or onto the session
host is a configuration change rather than a rewrite. `inference-corrections.md`
in this folder records what the provider's live API actually does, as against
what the architecture note assumed.

**What is not here yet.** Reporting the clinical outcome to `svc-core` at the
end of a call, and reporting an escalation the moment one is raised. Today the
loop ends the session record directly.
