# `app-dashboard` — clinician and admin screens

**No code yet.** The broad one: lists, patient records, one interview in
detail. Read what the agent found, act on it, sign it off. It reads and writes
through `svc-core` and holds an account credential via `shared/auth`.

It is also the piece that receives an escalation pushed mid-interview, which is
the one path where `svc-core` speaks first rather than answering a request.

`ux-clinical-dashboard.html` in this folder is the design spec.
