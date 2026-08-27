"""The patient's routes. No credential passes through this file.

Everything about the *call* lives in `services/core/lifecycle.py`; what is left
here is the HTTP: refuse, resolve, record, hand back a token. Every refusal
happens before a session record exists, so a turned-away caller leaves no row, no
room and no log file behind.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from services.agent.config.protocol import PROTOCOLS
from services.agent.session_log import ErrorEvent
from services.core import lifecycle
from services.core.config import (
    ACCEPTING_SESSIONS,
    FLY_MACHINE_ID,
    LIVEKIT_PUBLIC_URL,
    MAX_CONCURRENT_SESSIONS,
    MAX_SESSIONS_PER_DAY,
    RATE_LIMIT_BURST,
    RATE_LIMIT_WINDOW_S,
)
from services.core.limits import DailyQuota, RateLimiter
from services.core.queue import resolve_interview
from services.core.store import create_session, get_session, live_sessions
from shared.contracts.wire import SessionBootstrap

#: Marks a turn that arrived over HTTP rather than through the microphone, so
#: the record can say which it was. Everything else about the turn is identical.
TYPED_USER_ID = "patient-typed"

#: Said to the patient who arrives when the box is full. Deliberately not an
#: apology for a fault: the box holds a small number of concurrent calls by
#: construction, so this is the system working.
BUSY_MESSAGE = "All lines are busy at the moment. Please try again in a few minutes."

_starts = RateLimiter(burst=RATE_LIMIT_BURST, window_s=RATE_LIMIT_WINDOW_S)
_today = DailyQuota(limit=MAX_SESSIONS_PER_DAY)

router = APIRouter(tags=["patient"])


class TypedBody(BaseModel):
    text: str = ""


@router.post("/session")
async def start_session(request: Request):
    """Start an interview. Four things, and the order is the point:

    1. create the session record and a room name
    2. join that room from this process and wait until we are actually in it
    3. mint a token scoped to that room and that patient
    4. return the token

    The browser connects and finds the assistant already there. Steps 2 and 3
    are `lifecycle.start_call`, because their ordering is a property of the call
    rather than of this route.
    """
    # Four refusals, cheapest first, and every one of them lands before a
    # session record, a room or a log file exists. They all say the same
    # sentence: a caller who is turned away learns nothing about which limit
    # they hit, which is what stops the refusals from being a map of how to
    # get past them.
    if not ACCEPTING_SESSIONS:
        raise HTTPException(503, BUSY_MESSAGE, headers={"Retry-After": "3600"})

    caller = request.client.host if request.client else "unknown"
    if not _starts.allow(caller):
        raise HTTPException(
            429,
            BUSY_MESSAGE,
            headers={"Retry-After": str(_starts.retry_after(caller))},
        )

    # Per-IP is defeated by having more addresses; this is not. Without it the
    # caps still permit three calls back to back for as long as anyone cares to
    # run them, which on a link anyone can share is an open-ended bill.
    if not _today.allow():
        logger.warning(f"[session] refused {caller}: daily quota of {_today.limit} spent")
        raise HTTPException(503, BUSY_MESSAGE, headers={"Retry-After": "3600"})

    # Silero and SmartTurn run in-process on every 32 ms frame of every call, so
    # capacity is a physical property of the box. Refusing the next caller keeps
    # the calls already in progress intelligible; accepting them degrades all of
    # them at once.
    if len(live_sessions()) >= MAX_CONCURRENT_SESSIONS:
        logger.warning(f"[session] refused {caller}: at capacity")
        raise HTTPException(503, BUSY_MESSAGE, headers={"Retry-After": "60"})

    interview = await resolve_interview()
    protocol = PROTOCOLS.get(interview.protocol_id)
    if protocol is None:
        raise HTTPException(500, f"unknown protocol {interview.protocol_id}")

    # 1 ─ the record
    session = await create_session(interview, protocol)

    try:
        # 2 and 3 ─ join, wait, then mint what the patient connects with
        token = await lifecycle.start_call(session)
    except Exception as exc:
        logger.exception("[session] failed to start")
        session.writer.append(ErrorEvent(where="start", message=str(exc)))
        await lifecycle.teardown(session.id, "start_failed")
        raise HTTPException(500, str(exc)) from exc

    # 4 ─ hand it over. The *public* URL: this process may have dialled the SFU
    # on an address the patient's browser cannot resolve.
    return {
        "token": token,
        "url": LIVEKIT_PUBLIC_URL,
        "session": SessionBootstrap(
            session_id=session.id,
            room_name=session.room_name,
            # Where this call lives. `/typed` has to come back here, because
            # the session is a dict entry in *this* process.
            machine_id=FLY_MACHINE_ID or None,
            clinician=protocol.clinician,
            patient_first_name=interview.patient.first_name,
            fields=session.machine.fields(),
        ).model_dump(by_alias=True),
    }


@router.post("/session/{session_id}/typed")
async def submit_typed(session_id: str, body: TypedBody):
    """A typed answer takes exactly the same path as a spoken one.

    `loop.ts` had a live bug here: `submitTyped` skipped the `turnRunning` lock,
    so a typed answer during a voice turn ran a second concurrent turn and
    interleaved into history. Injecting the text as a frame hands the
    serialisation problem to the pipeline, which already solves it for speech.
    """
    session = get_session(session_id)
    if session is None or session.bot is None:
        raise HTTPException(404, "no such session")

    text = (body.text or "").strip()
    if text:
        from pipecat.frames.frames import TranscriptionFrame

        # A TranscriptionFrame, not an append-to-context frame. Appending
        # straight to the context would put typed text into the model without
        # passing the safety gate, which only ever sees transcripts — so a
        # patient could type the one thing the gate exists to catch and reach
        # the model with it. Entering as a transcript means the typed turn is
        # gated, logged and endpointed exactly like a spoken one.
        await session.bot.worker.queue_frames(
            [
                TranscriptionFrame(
                    text=text,
                    user_id=TYPED_USER_ID,
                    timestamp=datetime.now(UTC).isoformat(),
                )
            ]
        )
    return {"ok": True}


@router.post("/session/{session_id}/end")
async def end(session_id: str):
    await lifecycle.teardown(session_id, "ended_by_patient")
    return {"ok": True}
