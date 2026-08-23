"""The dispatch queue.

The app is dispatched from a queue. For now the queue is this list and
`POST /session` takes the head. Swapping it for a real queue — a database
table, a message broker — touches this file and nothing else, which is the
point of putting it behind a function rather than inlining the patient into
the session route.
"""

from shared.contracts.models import Patient, QueuedInterview

QUEUE: list[QueuedInterview] = [
    QueuedInterview(
        id="iv_0001",
        protocol_id="proto_warmup_v1",
        patient=Patient(id="pt_alice", first_name="Alice"),
    )
]


def next_interview() -> QueuedInterview:
    """Peek at the next interview without consuming it (re-runnable in dev)."""
    if not QUEUE:
        raise RuntimeError("dispatch queue is empty")
    return QUEUE[0]
