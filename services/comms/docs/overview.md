# `svc-comms` — everything that leaves the perimeter

**No code yet.** Invitation emails, reminders, scheduling. It **places** a call
and hands it to `svc-media`; it never carries one. Control plane, not data
plane.

The reason it is its own piece rather than a folder inside `svc-core`: one place
to audit, one place to swap a provider, one place a data-protection review has
to look at.
