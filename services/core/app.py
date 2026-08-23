"""The HTTP surface.

Four routes, unchanged from the Node service, because `frontend/call` is not
being changed by the migration.

What did change is what sits behind `POST /session`: the agent used to be an
in-process object this file constructed (`new ConversationLoop(...)`) and is now
a Pipecat pipeline started as an asyncio task. The ordering guarantee survives
that, and it is the only thing about this route that matters.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pipecat.pipeline.runner import WorkerRunner
from pydantic import BaseModel

from services.agent.config.protocol import PROTOCOLS
from services.agent.pipeline import build_bot
from services.agent.session_log import ErrorEvent, PatientJoined, RoomJoined
from services.core.config import (
    ALLOWED_ORIGINS,
    GROQ_API_KEY,
    LIVEKIT_URL,
    PORT,
)
from services.core.queue import next_interview
from services.core.store import Session, create_session, end_session, get_session, live_sessions
from services.core.tokens import mint_token
from shared.contracts.wire import SessionBootstrap

#: How long a patient's browser has to arrive before we give up on the room.
PATIENT_TIMEOUT_S = 30.0

#: Marks a turn that arrived over HTTP rather than through the microphone, so
#: the record can say which it was. Everything else about the turn is identical.
TYPED_USER_ID = "patient-typed"

_tasks: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY is not set — calls will fail at the first turn")
    logger.info(f"metafora · livekit {LIVEKIT_URL} · origins {ALLOWED_ORIGINS}")
    yield
    await drain("server_shutdown")


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


@app.get("/health")
async def health():
    return {"ok": True, "live": len(live_sessions()), "livekit": LIVEKIT_URL}


class TypedBody(BaseModel):
    text: str = ""


@app.post("/session")
async def start_session():
    """Start an interview. Four things, and the order is the point:

    1. create the session record and a room name
    2. join that room from this process and wait until we are actually in it
    3. mint a token scoped to that room and that patient
    4. return the token

    The browser connects and finds the assistant already there. Because we join
    before we hand out the token there is no race and no window in which a
    patient sits alone in an empty room.
    """
    interview = next_interview()
    protocol = PROTOCOLS.get(interview.protocol_id)
    if protocol is None:
        raise HTTPException(500, f"unknown protocol {interview.protocol_id}")

    # 1 ─ the record
    session = create_session(interview, protocol)

    try:
        async def _on_blocked(result) -> None:
            """A red flag stopped the turn before it reached a model.

            The sentence is already queued for the TTS by the gate; this ends
            the call behind it. `stop_when_done` drains rather than cancels, so
            the patient hears the line before the room closes.
            """
            session.ended_reason = "safety"
            await session.bot.wire.ended("safety", result.say)
            await session.bot.wire.phase("ended")
            await session.bot.worker.stop_when_done()

        # 2 ─ join, and wait
        bot = build_bot(
            protocol=protocol,
            interview=interview,
            machine=session.machine,
            writer=session.writer,
            room_name=session.room_name,
            token=mint_token(session.room_name, "assistant", can_publish=True),
            url=LIVEKIT_URL,
            api_key=GROQ_API_KEY,
            on_blocked=_on_blocked,
        )
        session.bot = bot
        await _wire_lifecycle(session, bot)

        connected = asyncio.Event()

        @bot.transport.event_handler("on_connected")
        async def _on_connected(_transport):
            connected.set()

        _tasks[session.id] = asyncio.create_task(_run(session, bot))
        await asyncio.wait_for(connected.wait(), timeout=PATIENT_TIMEOUT_S)
        session.writer.append(RoomJoined(identity="assistant"))

        # 3 ─ the patient's token, scoped to this room and this identity
        token = mint_token(
            session.room_name, f"patient-{interview.patient.id}", can_publish=True
        )
    except Exception as exc:
        logger.exception("[session] failed to start")
        session.writer.append(ErrorEvent(where="start", message=str(exc)))
        await teardown(session.id, "start_failed")
        raise HTTPException(500, str(exc)) from exc

    # 4 ─ hand it over
    return {
        "token": token,
        "url": LIVEKIT_URL,
        "session": SessionBootstrap(
            session_id=session.id,
            room_name=session.room_name,
            clinician=protocol.clinician,
            patient_first_name=interview.patient.first_name,
            fields=session.machine.fields(),
        ).model_dump(by_alias=True),
    }


async def _wire_lifecycle(session: Session, bot) -> None:
    @bot.transport.event_handler("on_first_participant_joined")
    async def _on_joined(_transport, participant):
        identity = _identity(participant)
        session.writer.append(PatientJoined(identity=identity))
        # Nothing may be said until the patient is in the room: data published
        # to a room they have not joined is not replayed to them on arrival, so
        # an opening spoken too early is spoken to nobody.
        await bot.wire.phase("thinking")
        await _speak_opening(session, bot)

    @bot.transport.event_handler("on_participant_disconnected")
    async def _on_left(_transport, participant, *_):
        await teardown(session.id, "patient_left")


def _identity(participant) -> str:
    if isinstance(participant, dict):
        return str(participant.get("identity") or participant.get("id") or "patient")
    return str(getattr(participant, "identity", "patient"))


async def _speak_opening(session: Session, bot) -> None:
    """The assistant speaks first.

    A patient who opened a link and tapped once should not be met with silence
    and left guessing whose turn it is.
    """
    from pipecat.frames.frames import TTSSpeakFrame

    from services.agent.session_log import OpeningSpoken

    state = session.machine.current
    if state is None:
        return
    clinician = session.protocol.clinician
    opening = " ".join(
        [
            f"Hello {session.interview.patient.first_name}.",
            f"{clinician.name} asked me to have a quick chat with you before your appointment.",
            "Just talk normally, and jump in whenever you like.",
            state.question.ask,
        ]
    )
    await bot.worker.queue_frames([TTSSpeakFrame(opening)])
    # The opening is spoken to the patient, so it belongs in the record as much
    # as anything the model generates later.
    session.writer.append(OpeningSpoken(text=opening, chunks=1, ms=0))


async def _run(session: Session, bot) -> None:
    runner = WorkerRunner(handle_sigint=False)
    try:
        await runner.run(bot.worker)
    except Exception as exc:
        logger.exception("[session] pipeline failed")
        session.writer.append(ErrorEvent(where="pipeline", message=str(exc)))
    finally:
        # The pipeline may have ended itself (interview complete, safety
        # closure): those reasons arrive on the writer, because the bot may not
        # reach the session record. Anything else is a pipeline that just ran
        # out of work.
        end_session(
            session, session.ended_reason or session.writer.ending_reason or "pipeline_finished"
        )
        # A pipeline-initiated end leaves the patient in a silent room unless
        # this process closes it: the browser was already told the call ended
        # (`wire.ended`), so dropping the room is the end, not an interruption.
        try:
            await bot.transport.disconnect()
        except Exception:
            logger.warning(f"[session] transport disconnect failed for {session.id}")


@app.post("/session/{session_id}/typed")
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


@app.post("/session/{session_id}/end")
async def end(session_id: str):
    await teardown(session_id, "ended_by_patient")
    return {"ok": True}


async def teardown(session_id: str, reason: str) -> None:
    """Idempotent: returns silently for a session that is already gone."""
    session = get_session(session_id)
    if session is None or session.ended:
        return
    end_session(session, reason)

    bot = session.bot
    if bot is not None:
        try:
            await bot.wire.ended(_wire_reason(reason))
            await bot.worker.stop_when_done()
        except Exception as exc:
            logger.warning(f"[session] teardown: {exc}")

    task = _tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


def _wire_reason(reason: str) -> str:
    if reason == "safety":
        return "safety"
    return "complete" if reason in {"complete", "ended_by_patient"} else "error"


async def drain(reason: str) -> None:
    """Stop accepting new sessions and let live ones finish.

    This process holds live WebRTC connections, so restarting it drops every
    call in progress. `stop_when_done()` sends an EndFrame and lets the pipeline
    finish what it is saying, which is the difference between a patient getting
    a goodbye and a patient getting silence.
    """
    sessions = live_sessions()
    if sessions:
        logger.info(f"draining {len(sessions)} live call(s)")
    await asyncio.gather(*(teardown(s.id, reason) for s in sessions), return_exceptions=True)


def main() -> None:
    """`uv run python -m services.core.app` — one process, the whole backend."""
    import uvicorn

    uvicorn.run("services.core.app:app", host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
