# `svc-core` — permissions, and everything a person asks for

Every read and write the dashboard and studio make goes through this service,
which is what keeps permissions in one place. It also pushes the things that
cannot wait to be asked for — an escalation raised mid-interview has to reach a
clinician in seconds.

**What runs today.** An Express process that starts an interview: it creates the
session record, joins the room from `@metafora/media`, mints the patient's token
*after* that join succeeds so nobody sits alone in an empty room, and hands the
token back. It also carries the typed-input and end-call routes.

**What is not here yet.** Accounts, roles, permission checks, and the stores.
The patient queue in `src/config/queue.ts` is a stand-in for `store-clinical`.

This is the process that imports the other two services, so today all three run
as one. Folders are not deployments; the split is here so that stops being true
without a rewrite.
