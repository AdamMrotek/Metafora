# Roadmap review — Phase 3 in particular

**Date:** 2026-08-25. A critical read of [`roadmap.md`](./roadmap.md) against the code.

**Status:** items 1, 2, 3, 4, 6, 7, 8, 10 and 11 are closed in code and docs. Item 5 (the
concurrency measurement) and item 9 (the email provider) are open — item 5 needs a deployed box
and is Phase 3's last step, item 9 belongs to Phase 5.

The order of the phases is right. Persistence and auth before the first deploy was a good call,
and every "why now" holds up. What follows is Phase 3, which is not ready to execute as written:
one bullet is factually wrong, two describe behaviour the code does not have, and the biggest
risk of putting this on a public URL is not mentioned at all.

Each item below is **what's wrong** and **what to do about it**.

---

## Must fix before deploying

### 1 · The list of Fly secrets is wrong — **closed**

**What's wrong.** `roadmap.md` says to set `DATABASE_URL`, `SUPABASE_URL` and `METAFORA_ENV=prod`,
and that "`config.py` refuses to import without the two that remain". It refuses on six things,
not two. `_problems()` in `services/core/config.py` also requires `GROQ_API_KEY`, real LiveKit
credentials (not `devkey`/`secret`), and a `LIVEKIT_PUBLIC_URL` that starts `wss://`.

Follow the bullet literally and the first boot is a crash.

**Fix.** Replace the bullet with the full list:

```
DATABASE_URL          Supabase session pooler, port 5432 (not 6543)
SUPABASE_URL          https://<project>.supabase.co
METAFORA_ENV          prod
GROQ_API_KEY          the one key the whole conversation runs on
LIVEKIT_API_KEY       from LiveKit Cloud
LIVEKIT_API_SECRET    from LiveKit Cloud
LIVEKIT_URL           wss://<project>.livekit.cloud
LIVEKIT_PUBLIC_URL    same, unless the browser dials somewhere else
ALLOWED_ORIGINS       only if the frontend gets its own origin (see item 3)
```

Keep the point the bullet was making — there is no JWT secret, which is what JWKS bought us.

---

### 2 · "Grace period longer than the longest interview" does not do anything — **closed**

**What's wrong.** The plan says to let old machines drain for longer than the longest possible
call. But `drain()` (`services/core/lifecycle.py:276`) does not wait for calls to finish — it
hangs every live call up straight away, politely, with a goodbye, and gives each one ten seconds
to say it. `lifespan` calls `drain()` on shutdown (`services/core/app.py:54`). So a shutdown
ends every call in about ten seconds and a fifteen-minute grace period is never used.

**Fix.** Pick one, and write down which:

- **Option A (recommended, no code).** Accept it. The goodbye *is* the drain. Set the shutdown
  grace to ~30 seconds and say so: on deploy, a patient mid-call hears the assistant say goodbye
  and the call ends. Not perfect, but it is honest, it is already built, and it is tested.
- **Option B (code).** Make `drain()` stop accepting new calls and then wait for live ones to end
  on their own, with the hang-up only as a timeout. That is real work and is not currently in the
  Phase 3 bullet list — add it if we want it.

Either way: check Fly's maximum `kill_timeout` before putting a large number in `fly.toml`.

---

### 3 · Blue/green will break the two by-id routes — **closed**

**What's wrong.** `deployment.md` §1 is clear that `/session/{id}/typed` and `/session/{id}/end`
have to reach the *same process* that served `POST /session`, or they 404. Blue/green means two
machines are up at the same time behind one Fly proxy, and the proxy will send those requests to
whichever machine it likes. "Stop routing new `POST /session` to the old one" is a wish, not a
mechanism.

This is the most likely thing to break on the first real deploy.

**Fix.** Pin the by-id requests to the right machine. Simplest version:

1. `POST /session` returns the machine id it is running on (Fly puts it in `FLY_MACHINE_ID`),
   alongside the session id and the LiveKit token.
2. `useCall.ts` sends it back on `/typed` and `/end` as the `fly-force-instance-id` header.
3. Add a test that the header round-trips, so nobody deletes it later as mystery code.

Alternative, if we want no frontend change: have the backend answer a request for an unknown
session id with Fly's `fly-replay` header pointing at the machine that owns it — but that means
a machine has to know which of its siblings owns a session, which means a lookup in Postgres.
Start with option 1.

---

### 4 · Nothing puts a ceiling on the bill — **closed in code; the Groq limit is manual**

**What's wrong.** The caps we have are per-IP (3 starts per 5 minutes) and concurrency (3 calls at
once, 15 minutes each). Per-IP is defeated by having more IPs. So the worst case is three calls
running back to back forever — roughly four hours of Groq speech-to-text, LLM and text-to-speech
*per hour*, indefinitely, from an unauthenticated public link. There is no daily limit, no total
counter, and no way to turn it off without a redeploy.

For a portfolio URL that anyone could post anywhere, this is the real risk. Bigger than the box
falling over.

**Fix.** Three small things, all in Phase 3:

- **A spend limit on the Groq account itself.** The one control that cannot be bypassed by a bug
  in our code. Do this first, it takes two minutes.
- **A daily session ceiling.** `MAX_SESSIONS_PER_DAY`, counted in Postgres (`clinical.interviews`
  already has the rows), refused with the same "all lines busy" sentence as the other two caps.
- **An off switch.** An env var (`ACCEPTING_SESSIONS=false`) that makes `POST /session` refuse,
  so shutting the demo down is a Fly secret change and not a code change.

Also: find out what LiveKit Cloud's free tier allows in participant-minutes, and what happens when
it runs out. Right now we do not know.

---

## Should fix

### 5 · Phase 3 never measures the number that decides whether it works — **open**

**What's wrong.** `deployment.md` §1 says concurrent-calls-per-vCPU is "a small number to be
measured, not guessed", and guesses 2–4. Phase 3 then ships one vCPU with the cap set at 3, and
defines success as *one* stranger on *one* phone.

Voice activity detection and turn detection run in our own process, per call, on every 32 ms of
audio. Three of those on one shared vCPU is where I would expect this to disappoint — and the
failure is not a clean refusal, it is all three calls getting slow together.

**Fix.** Add to the Done criteria: *three simultaneous calls hold their latency*. Run it once
against the deployed box (three browser tabs is enough), then set `MAX_CONCURRENT_SESSIONS` from
what we actually see. If it is 2, it is 2 — better to know.

---

### 6 · Error reporting is a third egress, and it can leak transcript text — **closed**

**What's wrong.** `CLAUDE.md` names two egresses, Groq and Supabase, as a convention worth
holding. Phase 3 adds Sentry or Logfire in half a line. And invariant 3 says nothing medical goes
to telemetry — but "every `ErrorEvent` reported" from the agent side can carry what the patient
said in the error context.

**Fix.** Either scope error reporting to `services/core` only (the HTTP layer, which never holds
transcript text), or write the scrubbing rule down as part of the phase and test it. And name the
third egress in `CLAUDE.md` in the same change, the way Supabase was named when it arrived.

---

### 7 · Phase 3 and Phase 5 contradict each other — **closed**

**What's wrong.** Phase 3 succeeds when "a stranger completes an interview on their phone" — that
is the no-token demo path. Phase 5 says the emailed link "replaces the ephemeral demo path", and
that `POST /session` takes a token. Read literally, Phase 5 deletes the portfolio demo, which is
the entire point of the project.

**Fix.** One sentence in Phase 5: the tokenless demo path stays, behind a flag
(`ALLOW_DEMO_SESSIONS`, default on). A token, when present, takes the dispatch path. Both end at
the same `claim()`, which is already true.

---

### 8 · Reconnect is in the wrong phase — **closed**

**What's wrong.** Mid-call resilience is parked in "6+ · Later". But a stranger on mobile data
losing the call at question 7, with no way back in, is the most likely way Phase 3's own success
criterion fails in front of someone we are trying to impress.

**Fix.** Move **reconnect / attach-and-resume** ahead of Phase 5's sign-off chain. The hash chain
is a lot of work and demonstrates less than a call that survives walking into a lift. Leave the
rest of item 6+ where it is.

---

## Worth a line each

### 9 · Phase 5 needs an email provider that nobody has chosen — **open, Phase 5**

It would be a fourth egress, and it is not named or costed in the phase that depends on it. Pick
one when Phase 5 starts (Resend and Supabase's own auth mailer are the obvious candidates) and
record it the way the others are recorded.

### 10 · Missing from Phase 3 — **closed**

- **Rollback.** The plan has a post-deploy smoke test but does not say what happens when it fails.
- **What the smoke test asserts.** At minimum: `/health` is ok, `POST /session` returns a token,
  and a clinical route 401s without a JWT and 200s with one.
- **Backup.** Supabase's default retention on the free tier, and whether we care.

### 11 · "Label the public URL as a demo" should be a consent line — **closed**

A label is decoration. One sentence on the opening screen doing real work instead: *this is a
demonstration on made-up data — please don't enter real personal or medical details, and nobody
is monitoring this conversation.* Related: red flags marked `urgent_escalate` go live to the
public in Phase 3, but the escalations table is Phase 5 and any way to *see* one is Phase 4. For
two phases, a flag firing is a database row nobody reads. Low stakes on a pre-anaesthetic
protocol with synthetic data — but the sentence above is what makes it low stakes, so write it.

---

## What is right and should not change

- Persistence and auth **before** the first deploy. Reversing the original "ship the thin version
  first" was correct: one deploy of something durable beats two of something that evaporates.
- JWKS instead of a shared secret. No symmetric credential in Fly secrets, and keys rotate
  without a redeploy.
- `config.accounts` seeded by a migration, never self-serve. On a public demo URL, signing up and
  being granted a caseload must not be the same act.
- LiveKit Cloud recorded in `deployment.md` §2 as a deliberate trade rather than an oversight,
  with the paragraph to revisit named.
- Phase order 3 → 4 → 5. Deploy, then read path, then dispatch.

---

## Suggested Phase 3 bullet list, revised

1. Full secret list (item 1). Groq spend limit set before anything is public (item 4).
2. `Dockerfile` (uv, `--frozen`) + `fly.toml`: 1 vCPU / 1 GB, `min_machines_running = 1`, no volume.
3. LiveKit Cloud, and find out the free-tier ceiling.
4. Machine pinning for `/session/{id}/typed` and `/session/{id}/end` (item 3).
5. Shutdown behaviour decided and written down (item 2).
6. `MAX_SESSIONS_PER_DAY` + `ACCEPTING_SESSIONS` off switch (item 4).
7. `frontend/call` → Vercel or Cloudflare Pages, `/api/*` rewritten to the Fly host.
8. Error reporting, scoped so no transcript text leaves (item 6).
9. CI deploy job, smoke test with named assertions, and a rollback step (item 10).
10. Consent sentence on the opening screen (item 11).

**Done:** a stranger completes an interview on their phone · three at once hold their latency ·
the row survives a redeploy · a rollback has been performed at least once on purpose.
