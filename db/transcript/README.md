# `store-transcript`

Log type 1 — the conversation. Turn by turn: what the agent asked, what the
patient said, what it decided and why. Same permission gate as the clinical
record; this is medical data, no less sensitive.

Separate because it is bulky, append-only, and written by a different service —
the one that had the conversation. Today `svc-agent` writes it as JSONL under
`logs/`. No migrations yet.
