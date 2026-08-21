# `store-metrics`

Log type 2 — the system. Latency, error rates, call volumes, queue depth, which
step ran and how long it took. Readable by anyone operating the system, with no
clinical permission needed.

That is only possible if it contains **no medical data at all**. Enforce it by
what the metrics writer *accepts*: if the only way to record an event is a
closed set of typed fields, a transcript line has nowhere to go.

No migrations yet.
