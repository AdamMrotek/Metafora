#!/usr/bin/env bash
#
# The dashboard's contract, before the dashboard exists.
#
# Signs in against Supabase with a password grant, then calls the clinical
# routes with what comes back. Everything Phase 4 will do from a browser, in a
# form you can run against a deployment the moment it is up — which is the point
# of writing it now rather than discovering the contract by building a UI
# against it.
#
#   scripts/auth.sh                        # sign in, then GET /interviews
#   scripts/auth.sh /patients              # any clinical route
#   scripts/auth.sh /interviews/iv_abc123
#
# Reads SUPABASE_URL and SUPABASE_ANON_KEY from .env, and the credentials from
# the environment or a prompt. The password is never written to .env: it belongs
# to a person, not to the deployment.

set -euo pipefail

ROUTE="${1:-/interviews}"
API="${API_URL:-http://localhost:3000}"

# Same rule as services/core/config.py: a value already in the environment wins
# over the file, so this works against a deployment without editing anything.
if [ -f .env ]; then
  while IFS='=' read -r key value; do
    case "$key" in ''|'#'*) continue ;; esac
    key="${key// /}"
    value="${value%\"}"; value="${value#\"}"
    [ -z "${!key:-}" ] && export "$key=$value"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env || true)
fi

: "${SUPABASE_URL:?set SUPABASE_URL in .env — the project this signs in against}"
: "${SUPABASE_ANON_KEY:?set SUPABASE_ANON_KEY in .env — the public key the sign-in endpoint requires}"

if [ -z "${METAFORA_EMAIL:-}" ]; then
  read -rp "email: " METAFORA_EMAIL
fi
if [ -z "${METAFORA_PASSWORD:-}" ]; then
  read -rsp "password: " METAFORA_PASSWORD
  echo
fi

echo "→ signing in to ${SUPABASE_URL} as ${METAFORA_EMAIL}"
TOKEN=$(
  curl -sS -X POST "${SUPABASE_URL}/auth/v1/token?grant_type=password" \
    -H "apikey: ${SUPABASE_ANON_KEY}" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg e "$METAFORA_EMAIL" --arg p "$METAFORA_PASSWORD" \
          '{email: $e, password: $p}')" \
  | jq -r '.access_token // empty'
)

if [ -z "$TOKEN" ]; then
  echo "sign-in failed — no access_token came back." >&2
  echo "Check the user exists in the Supabase console (Authentication → Users)." >&2
  exit 1
fi

# What the backend will verify: the issuer, the audience and who this is. Printed
# because a 403 from the next call is almost always this email not matching the
# seeded row in config.accounts.
echo "→ token claims"
cut -d. -f2 <<<"$TOKEN" \
  | tr '_-' '/+' \
  | { read -r p; printf '%s' "$p$(printf '=%.0s' $(seq $(( (4 - ${#p} % 4) % 4 ))))"; } \
  | base64 -d 2>/dev/null \
  | jq '{iss, aud, sub, email, exp}'

echo "→ GET ${API}${ROUTE}"
curl -sS -D /tmp/metafora-auth-headers "${API}${ROUTE}" \
  -H "Authorization: Bearer ${TOKEN}" | jq .
head -1 /tmp/metafora-auth-headers

echo "→ GET ${API}${ROUTE} with no token (must refuse)"
curl -sS -o /dev/null -w '  %{http_code}\n' "${API}${ROUTE}"
