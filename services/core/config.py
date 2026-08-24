"""Environment. Dev defaults match `livekit-server --dev`, so the repo runs
with an empty `.env` except for the Groq key.

Those same defaults are the reason this file verifies itself. `devkey`/`secret`
are published credentials that mint *valid* LiveKit tokens, so a production box
that boots with them is not broken in any way it can notice — it works, for
anyone. Outside dev the defaults are refused at import (§`_verify`), which is
the last point where the failure is a stack trace rather than a patient.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Node read `.env` via `tsx --env-file`; Python has no equivalent flag.

    Deliberately does not overwrite anything already in the environment, so a
    real deployment's secrets always win over a file left in the working tree.
    """
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

#: `dev` tolerates the published SFU credentials and a missing Groq key, because
#: `make dev` has to run on a laptop that has neither. Anything else is a real
#: deployment and tolerates neither.
ENV = os.environ.get("METAFORA_ENV", "dev")
IS_DEV = ENV == "dev"

PORT = int(os.environ.get("PORT", "3000"))

#: What *this process* dials to reach the SFU.
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")

#: What the *browser* is handed in the `POST /session` response. The same string
#: in dev, and deliberately a separate knob in production: the backend may reach
#: the SFU on an internal address the patient's browser cannot resolve, and one
#: variable cannot hold both answers.
LIVEKIT_PUBLIC_URL = os.environ.get("LIVEKIT_PUBLIC_URL", LIVEKIT_URL)

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

#: Supabase Postgres. Empty in dev means the JSONL writer stays the record and
#: the store stays in-process — `make dev` still runs on a laptop with nothing
#: provisioned. Outside dev it is required, because a deployed system that
#: forgets everything on restart is the failure Phase 1 exists to remove.
#:
#: Use the *session* pooler (port 5432), not the transaction pooler (6543):
#: asyncpg prepares statements, and transaction mode breaks them.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

#: The frontend stops sharing an origin once it deploys separately, so its
#: origin has to be named rather than implied by a Vite proxy.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

# ─── Caps ────────────────────────────────────────────────────────────────────
#
# `POST /session` is unauthenticated and starts an LLM + TTS session on every
# hit, so these are the only thing standing between a shared link and an
# unbounded Groq bill. The concurrency number is also a physical fact: Silero
# and SmartTurn run in-process on every 32 ms frame, so the box holds a small
# number of calls whatever we set here.

#: Concurrent live calls. Refuse the next one rather than degrade all of them.
MAX_CONCURRENT_SESSIONS = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))

#: Hard ceiling on one call. An interview is minutes; anything approaching this
#: is a browser tab someone left open, and it is holding a concurrency slot.
MAX_CALL_SECONDS = int(os.environ.get("MAX_CALL_SECONDS", "900"))

#: Per-IP rate limit on session starts: `RATE_LIMIT_BURST` immediately, then one
#: more every `RATE_LIMIT_WINDOW_S / RATE_LIMIT_BURST` seconds.
RATE_LIMIT_BURST = int(os.environ.get("RATE_LIMIT_BURST", "3"))
RATE_LIMIT_WINDOW_S = float(os.environ.get("RATE_LIMIT_WINDOW_S", "300"))


# ─── Verification ────────────────────────────────────────────────────────────

#: What `livekit-server --dev` publishes, and therefore what everyone has.
_DEV_LIVEKIT_CREDENTIALS = ("devkey", "secret")


class ConfigError(RuntimeError):
    """Raised at import when a non-dev environment is misconfigured."""


def _problems() -> list[str]:
    """Every reason this environment must not serve a patient."""
    found = []
    if not GROQ_API_KEY:
        found.append(
            "GROQ_API_KEY is empty — the first turn of the first call would fail "
            "mid-sentence in front of a patient"
        )
    if (LIVEKIT_API_KEY, LIVEKIT_API_SECRET) == _DEV_LIVEKIT_CREDENTIALS:
        found.append(
            "LIVEKIT_API_KEY/LIVEKIT_API_SECRET are still the published "
            "`livekit-server --dev` credentials — anyone can mint a valid token"
        )
    if not DATABASE_URL:
        found.append(
            "DATABASE_URL is empty — the deployment would write its only record "
            "to a container filesystem that the next release deletes"
        )
    if LIVEKIT_PUBLIC_URL.startswith("ws://"):
        found.append(
            f"LIVEKIT_PUBLIC_URL is {LIVEKIT_PUBLIC_URL!r} — a browser on https "
            "will refuse an insecure websocket; it must be wss://"
        )
    return found


def _verify() -> None:
    """Fail loudly at import, which is the only moment nobody is on the line."""
    if IS_DEV:
        return
    problems = _problems()
    if problems:
        raise ConfigError(
            f"METAFORA_ENV={ENV!r} but the environment is not deployable:\n"
            + "\n".join(f"  · {p}" for p in problems)
        )


_verify()
