#!/usr/bin/env bash
#
# Does the deployed backend actually work?
#
# Runs after every deploy, and its failure is what triggers the rollback. So it
# asserts the things a green health check does not: that the process reached
# Supabase, that it can genuinely start a call, and that the clinical routes are
# still shut to an anonymous caller.
#
#   scripts/smoke.sh https://metafora.fly.dev
#
# Needs no credentials by design — a smoke test that requires a password is one
# that gets skipped in CI, and the assertions below are the ones that matter
# without one. `scripts/auth.sh` covers the authenticated half by hand.
#
# Test 2 starts a real session, which costs one LiveKit room and one opening
# utterance of Groq TTS. That is the price of proving the deploy works rather
# than proving the process is running, and it is the only assertion here that
# exercises Groq, LiveKit and Postgres in one go. It ends the session it starts.

set -euo pipefail

BASE="${1:-}"
if [ -z "$BASE" ]; then
  echo "usage: scripts/smoke.sh <base-url>" >&2
  exit 2
fi
BASE="${BASE%/}"

fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*" >&2; exit 1; }
pass() { printf '  \033[32mok\033[0m    %s\n' "$*"; }

echo "smoke · $BASE"

# ─── 1 · the process is up, and it found its project ─────────────────────────
#
# `auth: true` is the load-bearing half. `ok: true` only says Python is running;
# auth is false whenever SUPABASE_URL never reached the process, which is a
# deployment where every clinical route answers 503 and the health check is
# green anyway. That is exactly the failure a health check alone cannot see.
HEALTH="$(curl -fsS --max-time 10 "$BASE/health")" || fail "GET /health did not answer"
echo "$HEALTH" | grep -q '"ok":true'   || fail "GET /health is not ok: $HEALTH"
echo "$HEALTH" | grep -q '"auth":true' || fail "auth is not configured — SUPABASE_URL did not arrive: $HEALTH"
pass "/health · $HEALTH"

# ─── 2 · a call can actually start ───────────────────────────────────────────
#
# Groq (the opening line), LiveKit (the room and the join) and Postgres (the
# claimed row) all in one request. If this passes, the deploy is real.
START="$(curl -fsS --max-time 45 -X POST "$BASE/session")" \
  || fail "POST /session failed — the deploy cannot start a call"

TOKEN="$(printf '%s' "$START" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
SESSION_ID="$(printf '%s' "$START" | sed -n 's/.*"sessionId":"\([^"]*\)".*/\1/p')"
[ -n "$TOKEN" ]      || fail "POST /session returned no token: $START"
[ -n "$SESSION_ID" ] || fail "POST /session returned no sessionId: $START"
pass "POST /session · $SESSION_ID"

# Leave nothing running. A smoke test that holds a concurrency slot for fifteen
# minutes takes a third of the box's capacity with it.
MACHINE_ID="$(printf '%s' "$START" | sed -n 's/.*"machineId":"\([^"]*\)".*/\1/p')"
END_ARGS=(-fsS --max-time 30 -X POST "$BASE/session/$SESSION_ID/end")
# An `if`, not `[ ... ] &&`: under `set -e` a false test as the last command of
# a line ends the script, and "no machine id" is the normal case off Fly.
if [ -n "$MACHINE_ID" ]; then
  END_ARGS+=(-H "fly-force-instance-id: $MACHINE_ID")
fi
curl "${END_ARGS[@]}" >/dev/null || fail "POST /session/$SESSION_ID/end failed — the call is still running"
pass "POST /session/$SESSION_ID/end"

# ─── 3 · the clinical routes are still shut ──────────────────────────────────
#
# The one assertion here that is about safety rather than function. A deploy
# that silently opens the read surface looks completely healthy from tests 1
# and 2.
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/interviews")"
[ "$CODE" = "401" ] || fail "GET /interviews with no token returned $CODE, expected 401"
pass "GET /interviews · 401 without a token"

echo "smoke · all passed"
