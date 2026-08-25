"""A call, from joining the room to the last word of the goodbye.

Lifted out of `app.py` by the Phase 2 split, and not only to keep the routers
thin. `drain()` is called from `lifespan`, and `teardown` / `_run` /
`_speak_opening` / `_expire` are shared between the session routes and that
drain — left in the session router they would have had to be imported back out
of it by `app.py`, which is the import cycle the split exists to avoid.

Nothing here is authenticated and nothing here should be: a patient arrives with
a link and never holds a credential.
"""

import asyncio

from loguru import logger
from pipecat.pipeline.runner import WorkerRunner

from services.agent.pipeline import build_bot
from services.agent.session_log import ErrorEvent, PatientJoined, RoomJoined
from services.core.config import (
    GROQ_API_KEY,
    LIVEKIT_URL,
    MAX_CALL_SECONDS,
)
from services.core.store import Session, end_session, get_session, live_sessions
from services.core.tokens import mint_token

#: How long a patient's browser has to arrive before we give up on the room.
PATIENT_TIMEOUT_S = 30.0

#: How long the goodbye may take before we stop waiting for it.
#:
#: `stop_when_done()` only *queues* an EndFrame and returns (pipecat
#: `pipeline/worker.py:659`) — it does not wait for the pipeline to finish
#: speaking. Cancelling the runner straight after it therefore cuts the sentence
#: off, which is exactly what `drain()` promises not to do. So teardown waits
#: for the runner task to end on its own, and this is the bound on that wait: a
#: pipeline wedged on a hung TTS call must not hold a deploy open forever.
GOODBYE_TIMEOUT_S = 10.0

_tasks: dict[str, asyncio.Task] = {}
_watchdogs: dict[str, asyncio.Task] = {}


async def start_call(session: Session) -> str:
    """Join the room, wait until we are actually in it, and return the patient's token.

    The order is the whole point. The assistant is connected before the patient
    holds anything they could connect with, so there is no window in which
    someone sits alone in an empty room. Both mints live here for the same
    reason: the ordering is a property of this function, not of a route.
    """

    async def _on_blocked(result) -> None:
        """A red flag stopped the turn before it reached a model.

        The sentence is already queued for the TTS by the gate; this ends the
        call behind it. `stop_when_done` drains rather than cancels, so the
        patient hears the line before the room closes.
        """
        session.ended_reason = "safety"
        await session.bot.wire.ended("safety", result.say)
        await session.bot.wire.phase("ended")
        await session.bot.worker.stop_when_done()

    bot = build_bot(
        protocol=session.protocol,
        interview=session.interview,
        machine=session.machine,
        writer=session.writer,
        room_name=session.room_name,
        token=mint_token(session.room_name, "assistant", can_publish=True),
        url=LIVEKIT_URL,
        api_key=GROQ_API_KEY,
        on_blocked=_on_blocked,
    )
    session.bot = bot
    _wire_lifecycle(session, bot)

    connected = asyncio.Event()

    @bot.transport.event_handler("on_connected")
    async def _on_connected(_transport):
        connected.set()

    _tasks[session.id] = asyncio.create_task(_run(session, bot))
    _watchdogs[session.id] = asyncio.create_task(_expire(session.id))
    await asyncio.wait_for(connected.wait(), timeout=PATIENT_TIMEOUT_S)
    session.writer.append(RoomJoined(identity="assistant"))

    return mint_token(
        session.room_name, f"patient-{session.interview.patient.id}", can_publish=True
    )


def _wire_lifecycle(session: Session, bot) -> None:
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
        await end_session(
            session, session.ended_reason or session.writer.ending_reason or "pipeline_finished"
        )
        # A pipeline-initiated end leaves the patient in a silent room unless
        # this process closes it: the browser was already told the call ended
        # (`wire.ended`), so dropping the room is the end, not an interruption.
        try:
            await bot.transport.disconnect()
        except Exception:
            logger.warning(f"[session] transport disconnect failed for {session.id}")
        # Last, because everything above can still append. Idempotent, and
        # `teardown` closes too — a pipeline that ends itself never reaches
        # `teardown`, and a call that failed to start never reaches here.
        await session.writer.close()


async def _expire(session_id: str) -> None:
    """Hang up a call that has run past `MAX_CALL_SECONDS`.

    An interview is minutes. Anything near the ceiling is a tab someone left
    open, and it is holding one of very few concurrency slots — so this is a
    capacity guard as much as a cost one. Ending through `teardown` means an
    expired call gets the same goodbye and the same record as any other.
    """
    try:
        await asyncio.sleep(MAX_CALL_SECONDS)
    except asyncio.CancelledError:
        return
    if get_session(session_id) is not None:
        logger.warning(f"[session] {session_id} hit the {MAX_CALL_SECONDS}s ceiling")
        await teardown(session_id, "max_duration")


async def teardown(session_id: str, reason: str) -> None:
    """Idempotent: returns silently for a session that is already gone."""
    session = get_session(session_id)
    if session is None or session.ended:
        return
    await end_session(session, reason)

    bot = session.bot
    if bot is not None:
        try:
            await bot.wire.ended(_wire_reason(reason))
            await bot.worker.stop_when_done()
        except Exception as exc:
            logger.warning(f"[session] teardown: {exc}")

    watchdog = _watchdogs.pop(session_id, None)
    if watchdog and not watchdog.done():
        watchdog.cancel()

    task = _tasks.pop(session_id, None)
    await _await_goodbye(session_id, task)
    # Only when no pipeline ever ran — a call that failed to start. Otherwise
    # `_run`'s finally is the authoritative close, and it has to be: reached
    # from inside the pipeline's own task (the patient hanging up) the wait
    # above returns immediately, and closing here would drop the goodbye's own
    # events on the floor.
    if task is None:
        await session.writer.close()


async def _await_goodbye(session_id: str, task: asyncio.Task | None) -> None:
    """Let the pipeline finish the sentence it is on, then cancel it.

    The EndFrame queued by `stop_when_done()` still has to travel the length of
    the pipeline and out through the transport. The runner task ending is the
    only signal that it arrived, so that is what we wait on.
    """
    if task is None or task.done():
        return

    if task is asyncio.current_task():
        # We were reached from inside the pipeline's own task — a transport
        # event handler, typically the patient hanging up. Waiting here would be
        # waiting on ourselves. The EndFrame is queued; the task ends itself.
        return

    try:
        # Shielded, so a timeout leaves the task alive for us to cancel
        # deliberately rather than having `wait_for` cancel it mid-sentence.
        await asyncio.wait_for(asyncio.shield(task), timeout=GOODBYE_TIMEOUT_S)
    except TimeoutError:
        logger.warning(
            f"[session] {session_id} did not finish speaking within "
            f"{GOODBYE_TIMEOUT_S}s — cancelling"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"[session] {session_id} pipeline ended badly: {exc}")

    if not task.done():
        task.cancel()
        # `cancel()` only *requests* it. On shutdown we are about to stop the
        # loop, so wait for the cancellation to actually land rather than
        # leaving a half-unwound pipeline behind.
        await asyncio.gather(task, return_exceptions=True)


def _wire_reason(reason: str) -> str:
    """Why the call ended, in the vocabulary the portal renders.

    A shutdown or the duration ceiling is not an error: nothing failed, this
    side chose to stop. Phase 3's blue/green makes shutdown routine, so the
    patient hearing it framed as a fault would be wrong most of the time it
    happens.
    """
    if reason == "safety":
        return "safety"
    if reason in {"complete", "ended_by_patient"}:
        return "complete"
    if reason in {"server_shutdown", "max_duration"}:
        return "interrupted"
    return "error"


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
