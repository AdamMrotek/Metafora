# `shared-contracts` — the shapes passed between pieces

One definition, both sides. Published as `@metafora/contracts` and depended on
by every other package; it depends on nothing.

Three things live here:

- **`types.ts`** — the interview definition and the clinical record shapes.
- **`wire.ts`** — what `svc-media` puts on the data channel and `app-call`
  reads off it.
- **`audio.ts`** — the frame format `svc-media` and `svc-agent` agree on. It is
  here rather than in either service because both need it and neither owns it.

When `db/` exists, types are generated one way from the migrations into this
package — never hand-copied, never imported backwards.
