"""Environment. Dev defaults match `livekit-server --dev`, so the repo runs
with an empty `.env` except for the Groq key."""

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

PORT = int(os.environ.get("PORT", "3000"))
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

#: The frontend stops sharing an origin once it deploys separately, so its
#: origin has to be named rather than implied by a Vite proxy.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
