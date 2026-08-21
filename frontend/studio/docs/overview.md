# `app-studio` — authoring and evals

**No code yet.** Author an interview, version it, run it against test cases
before it goes live. Evals live here.

What it authors is the interview definition that `svc-agent` runs. Today that
definition is a hardcoded constant in `services/agent/src/config/protocol.ts`;
when this app exists, it becomes a record in `store-config` and the constant
goes away.

`ux-agent-studio.html` in this folder is the design spec.
