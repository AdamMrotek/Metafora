# ADR — one pass, one tool, and the sentence rides in the call

**Status:** implemented, and the only shape the pipeline has · 2 September 2026
**Code:** `services/agent/next_message.py`, `machine.tool_definitions`, `prompts.system_prompt`

## Decision

gpt-oss emits speech *or* a tool call, never both. So there is **one LLM pass**, holding
**one tool**, told to answer *through* it: `update_intake` takes a required, last-position
`message_next` — what to say out loud once this answer is recorded. The record and the
reply are one emission, so they cannot disagree.

## How a turn runs

```
input → vad → stt → SafetyGate → turn → user ctx → llm (tools) → NextMessage → ending → tts → output → assistant ctx
```

1. Model emits `update_intake(field, value, answer?, flag?, message_next="…")`.
2. `tools.dispatch` **writes the record first** — permission matrix, `machine.position`,
   `_is_thin`, capture, concerns — and returns `{"ok": …}`, plus `"interview": "ending"`
   when a concern stopped the call.
3. `NextMessage` re-reads `message_next` off the result frame's arguments and speaks it as
   an ordinary response — but only if the result allows. A **refused** call withholds it
   (the machine did not move); an **ending** one withholds it too (a stopping call says the
   protocol's authored sentence, never a model's).
4. A turn that records nothing — a follow-up, a question back — is plain text and streams
   normally.

## Endings

There is no hangup tool. A call ends because every field is captured (`machine.complete`)
or because a flag authored at `action="end_call"` stopped it, in which case
`EndOfInterview` speaks that flag's `say` verbatim and hangs up behind it. Both are decided
in this process; the model cannot end a call early or refuse to end one.
`services/agent/config/protocol.py` lists every flag that can.

## Consequences

- `system_prompt` names the tool *and* says how to talk, because the pass is not heard —
  only its `message_next` is. It must therefore be told never to mention a concern out
  loud. `tests/test_prompts.py` holds that.
- A refused call is a turn with nothing recorded and nothing said, so the handler sets
  `run_llm=True` **once per patient turn** to let the model see the error and answer again.
- Cost: first-token latency. Nothing is spoken until the tool call's JSON is complete.
- Risk: it rests on the model reliably filling `message_next`. `NextMessage` logs a warning
  when a call arrives without one.
