# `svc-media` — the only thing a patient connects to

Terminates the connection, carries the audio, and lets an agent session attach
to it. Transport only: no clinical logic, and it never sees a transcript.

**What runs today.** `AssistantRoom` — this process as a participant in the
call, not a service standing beside it. LiveKit is used strictly as a WebRTC
transport, and nothing above `room.ts` knows it exists.

**What is not here yet.** The two-leg split. Today the patient's connection and
the agent session terminate in the same process, so an agent host dying is a
dial tone rather than a pause. Splitting them is what makes attach-and-resume
possible, and it needs three things: this service minting and holding the
session id, `store-transcript` enforcing the write fence, and the dashboard
reading the recovery write rather than a second progress record.

Telephony also lands here, once `svc-comms` can place a call.
