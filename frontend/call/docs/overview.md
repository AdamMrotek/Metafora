# `app-call` — the patient's one screen

**Built.** Join, talk, see what was captured. Entered from a link, not a login:
this app deliberately does not use `shared/auth`, and a patient never holds an
account credential.

It asks `svc-core` for a session, gets back a token scoped to one room and one
identity, and connects to `svc-media`. Everything it renders after that arrives
over the data channel as `@metafora/contracts` messages.

`ux-patient-portal.html` in this folder is the design it was built from — a
static prototype, kept as the reference for what the screen is meant to be.
