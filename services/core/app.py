"""The HTTP surface — the application, not the routes.

What is left here after the Phase 2 split is everything that is true of the
process rather than of one endpoint: the lifespan, CORS, the error body shape
the patient portal reads, and `/health`. The routes live in
`services/core/routes/`, split by audience because the two audiences have
opposite auth postures — a patient never holds a credential, and every clinical
read refuses one that is missing.

`services/core/lifecycle.py` holds the call itself. Both this file (`drain` in
`lifespan`) and the session router reach it, which is why it is neither.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from services.core import db
from services.core.config import (
    ALLOWED_ORIGINS,
    GROQ_API_KEY,
    JWKS_URL,
    JWT_ISSUER,
    LIVEKIT_PUBLIC_URL,
    LIVEKIT_URL,
    MAX_CONCURRENT_SESSIONS,
    PORT,
    SUPABASE_URL,
)
from services.core.lifecycle import drain
from services.core.routes import interviews, patients, session
from services.core.store import live_sessions
from shared import auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY is not set — calls will fail at the first turn")
    # The record before the first caller: `clinical.interviews` references
    # `config.protocols`, so nothing can be dispatched until it is populated.
    await db.connect()
    await db.seed_protocols()
    if not db.enabled():
        logger.warning("no DATABASE_URL — this process forgets every call when it restarts")
    await _configure_auth()
    logger.info(
        f"metafora · livekit {LIVEKIT_URL} (browsers dial {LIVEKIT_PUBLIC_URL}) "
        f"· origins {ALLOWED_ORIGINS} · cap {MAX_CONCURRENT_SESSIONS} calls"
    )
    yield
    await drain("server_shutdown")
    auth.configure(None)
    await db.close()


async def _configure_auth() -> None:
    """Assemble the door, or leave it shut.

    `shared/auth` is handed its issuer, its keys and its account directory from
    here rather than reading the environment itself — that is what keeps it a
    base layer instead of a second copy of this application, and it is what lets
    `tests/test_auth.py` stand a whole authenticated backend up with an EC
    keypair and no project.

    Not configured means every clinical route answers 503. That is the dev
    default and it is a refusal, never an open door; `config.py` refuses to
    import outside dev without `SUPABASE_URL`.
    """
    if not SUPABASE_URL:
        auth.configure(None)
        logger.warning("no SUPABASE_URL — the clinical routes will refuse every caller with 503")
        return

    keys = auth.Jwks(JWKS_URL)
    try:
        # Pre-warmed so the first clinician of the morning does not pay for the
        # fetch, and so a wrong project is a line in this log rather than a 401
        # nobody can explain. Not fatal: the keys are re-fetched on demand.
        await keys.warm()
    except Exception as exc:
        logger.warning(f"could not pre-fetch the signing keys from {JWKS_URL}: {exc}")
    auth.configure(
        auth.Authenticator(
            jwks=keys,
            issuer=JWT_ISSUER,
            accounts=auth.PostgresAccounts(db.pool),
        )
    )
    logger.info(f"auth · verifying tokens issued by {JWT_ISSUER}")


app = FastAPI(lifespan=lifespan)

# The frontend stops sharing an origin once it deploys separately, so the
# origin is named rather than implied by a Vite proxy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(session.router)
app.include_router(interviews.router)
app.include_router(patients.router)


@app.exception_handler(HTTPException)
async def _error_body(_request: Request, exc: HTTPException) -> JSONResponse:
    """`useCall.ts` reads `.error` off a failed response; FastAPI's default body
    says `.detail`, so every failure reached the patient as the generic "could
    not start". One handler, and the message we wrote is the message they see.

    It carries the clinical routes' refusals too: a 403 that says *why* — a
    verified token naming nobody in `config.accounts` — is only useful if the
    reason survives to the other end.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.get("/health")
async def health():
    """Unauthenticated, and stays that way: it is the Fly check, and it says
    nothing about any patient."""
    return {
        "ok": True,
        "live": len(live_sessions()),
        "capacity": MAX_CONCURRENT_SESSIONS,
        "livekit": LIVEKIT_URL,
        # An operator needs to know whether this box can answer a clinician at
        # all. Whether the door is installed, never who came through it.
        "auth": auth.configured(),
    }


def main() -> None:
    """`uv run python -m services.core.app` — one process, the whole backend."""
    import uvicorn

    uvicorn.run("services.core.app:app", host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
