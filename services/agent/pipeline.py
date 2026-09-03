"""The conversation, as a Pipecat pipeline.

`loop.ts` was 623 lines because it hand-rolled endpointing, barge-in, sentence
chunking, synthesise-ahead playback, SSE parsing and WAV handling. All of that
is framework default now. What is left here is the ordering — and the ordering
is the architecture:

    transport.input → vad → stt → SAFETY GATE → turn → context → llm (tools)
                                                                        ↓
                                                                  NEXT MESSAGE
                                                                        ↓
    transport.output ←── tts (trimmed, Orpheus-capped) ←── ending ←──────┘

The transcript passes through our code before it reaches the model, so the gate
cannot be bypassed. Everything the assistant says leaves from the same process,
so the session log is a first-hand record rather than a reconstruction of
whatever the browser chose to relay.

One model, holding the tools, answering *through* them. gpt-oss emits speech or
a tool call and never both, which this used to answer by splitting the turn
across two passes running side by side — one that spoke and held no tools, one
that held the tools and was never heard. The split bought an immediate first
sentence and cost the thing a clinical interview cannot afford: the two could
not see each other. On the closing question one of them recorded the last field
and ended the call while the other asked the patient what they wanted to say.

So the sentence rides in the tool call instead. `update_intake` carries a
required `message_next`, and `next_message.py` releases it into the speech path
once `dispatch` has written the record and ruled on it. The record and the reply
are one decision, made once, in that order. See `next_message.py` for what it
costs — first-token latency, paid on every recorded turn.
"""

from dataclasses import dataclass

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.llm_service import (
    FunctionCallParams,
    FunctionCallResultProperties,
)
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

from services.agent.config import tuning
from services.agent.end_call import EndOfInterview
from services.agent.gate import SafetyGate
from services.agent.machine import InterviewMachine
from services.agent.next_message import NextMessage
from services.agent.observer import SessionLogObserver
from services.agent.prompts import system_prompt
from services.agent.session_log import SessionWriter
from services.agent.tools import dispatch
from services.agent.tts import TrimmedGroqTTSService
from services.agent.tts_text import OrpheusAggregator
from services.agent.wire import Wire, WireObserver
from shared.contracts.models import ProtocolVersion, QueuedInterview


def endpointing_vad() -> SileroVADAnalyzer:
    """Speech detection, for the pipeline's single `VADProcessor`.

    Note that this no longer decides when a turn *ends*. In Pipecat 1.7 the
    user-turn *stop* decision belongs to the turn strategy —
    `LocalSmartTurnAnalyzerV3` by default — not to `stop_secs`. The VAD still
    detects speech onset and drives barge-in, so `start_secs` and `confidence`
    are live; `stop_secs` is kept at our stated 0.7 s so that VAD-derived
    silence timing matches the number `tuning.py` argues for, and so the
    measurement in `observer.py` compares like with like.

    We are deliberately running the framework's semantic turn detection rather
    than our own floor, and measuring the difference on real calls before
    committing either way. See `EndpointDecision` in `session_log.py`.
    """
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=tuning.SPEECH_PROBABILITY,
            start_secs=tuning.SPEECH_START_MS / 1000,
            stop_secs=tuning.ENDPOINT_SILENCE_MS / 1000,
        )
    )


def told_the_turn() -> LLMUserAggregatorParams:
    """User-aggregator params for a context that is *told* when the turn ended.

    Left to its defaults the aggregator decides the turn itself, which means a
    `SileroVADAnalyzer` and a `LocalSmartTurnAnalyzerV3` of its own, on top of
    the ones `UserTurnProcessor` already runs: two ONNX sessions scoring every
    frame of the patient's audio, two end-of-turn verdicts that nothing
    reconciles, and two `InterruptionFrame`s per barge-in.
    `ExternalUserTurnStrategies` makes it wait to be told instead — it detects
    nothing, emits no `UserStartedSpeakingFrame`, and raises no interruption.
    The turn belongs to the patient, and it is decided once, upstream.
    """
    return LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies())


@dataclass
class Bot:
    worker: PipelineWorker
    transport: LiveKitTransport
    wire: Wire
    machine: InterviewMachine


def build_bot(
    *,
    protocol: ProtocolVersion,
    interview: QueuedInterview,
    machine: InterviewMachine,
    writer: SessionWriter,
    room_name: str,
    token: str,
    url: str,
    api_key: str,
    on_blocked=None,
) -> Bot:
    transport = LiveKitTransport(
        url=url,
        token=token,
        room_name=room_name,
        params=LiveKitParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    wire = Wire(transport)

    stt = GroqSTTService(
        api_key=api_key, settings=GroqSTTService.Settings(model=tuning.STT_MODEL)
    )

    # Both are required for voice: `hidden` keeps chain-of-thought out of
    # `content`, `low` keeps the first token inside the latency budget.
    def _llm() -> GroqLLMService:
        return GroqLLMService(
            api_key=api_key,
            settings=GroqLLMService.Settings(model=tuning.LLM_MODEL),
            params=GroqLLMService.InputParams(
                extra={"reasoning_format": "hidden", "reasoning_effort": "low"}
            ),
        )

    llm = _llm()

    tts = TrimmedGroqTTSService(
        api_key=api_key,
        settings=TrimmedGroqTTSService.Settings(
            model=tuning.TTS_MODEL,
            # Groq's default voice is `autumn`; the clinician's is not.
            voice=tuning.TTS_VOICE,
        ),
        # Orpheus rejects input over 200 characters and no built-in aggregator
        # enforces a hard cap, so this is not optional.
        text_aggregator=OrpheusAggregator(),
    )

    # Constructed here rather than inline in the pipeline below, because the
    # tool handler needs a handle on it: a question flag that stops the call is
    # decided on the function-call task and can only be *spoken* from this one.
    ending = EndOfInterview(machine, writer, wire)

    #: One re-run per patient turn, and only after a *refused* call. See the
    #: `run_llm` argument below.
    retried_at_turn = -1

    async def _on_update_intake(params: FunctionCallParams) -> None:
        # The permission matrix runs in-process before anything is captured;
        # the result string is what the model sees, folded back into context.
        result = await dispatch(
            machine=machine,
            writer=writer,
            wire=wire,
            tool_name=params.function_name,
            arguments=params.arguments,
            on_concern=ending.answered,
        )
        # Pipecat defaults a tool result to `run_llm=True`: the aggregator
        # re-runs inference so a model can say something about what its call
        # returned. On a clean capture that is exactly wrong here — the model
        # has already said what it had to say, in `message_next`, and a re-run
        # sees a context it has answered, notices the question has moved on, and
        # records the previous turn against the next field, or invents one. On
        # `iv_eca23eefda25` it did both, and the invented answer to the closing
        # question completed the interview and hung up on a patient who had not
        # been given the chance to answer it.
        #
        # A *refused* call is the exception, and it is the only thing standing
        # between a refusal and a silent line: the sentence the patient hears is
        # an argument of the call, so a call the matrix turns down is a turn
        # with nothing recorded and nothing said. `next_message.py` withholds
        # that sentence — it was written for a question the machine never moved
        # past — and this gives the model one, and exactly one, chance per turn
        # to see the error and answer again. The bound is what keeps it from
        # becoming a loop: a second refusal on the same turn is simply logged.
        nonlocal retried_at_turn
        run_llm = False
        if not result.get("ok") and retried_at_turn != machine.turns:
            retried_at_turn = machine.turns
            run_llm = True

        await params.result_callback(
            result, properties=FunctionCallResultProperties(run_llm=run_llm)
        )

    # The tool table, compiled from the protocol: the model can only call what
    # `machine.tool_definitions()` advertises, and the handler on each schema
    # is auto-registered by the LLM service when the context is attached.
    tools = [
        FunctionSchema(
            name=definition["name"],
            description=definition["description"],
            properties=definition["parameters"]["properties"],
            required=definition["parameters"]["required"],
            handler=_on_update_intake,
        )
        for definition in machine.tool_definitions()
    ]

    # One context, holding the tools *and* the voice instructions — the two
    # things the old `system_prompt`/`capture_prompt` split existed to keep
    # apart. They are safe together because this pass is not heard: only
    # `message_next` is, lifted out of the arguments downstream, so the syntax
    # the split was protecting the patient from has nowhere to leak to.
    context = LLMContext(
        messages=[{"role": "system", "content": system_prompt(protocol, interview)}],
        tools=tools,
    )
    user, assistant = LLMContextAggregatorPair(context, user_params=told_the_turn())

    # ── one turn, decided once ──
    #
    # `TransportParams` carries no VAD in 1.7, so speech detection and turn
    # detection are processors now. Left inside the aggregator — where the
    # defaults put them — they duplicate what these two already do; see
    # `told_the_turn`. `UserTurnProcessor` keeps Pipecat's defaults: VAD and
    # transcription for the start, `LocalSmartTurnAnalyzerV3` for the stop, so
    # the behaviour is the one `tuning.py` describes and `EndpointDecision` is
    # measuring.
    vad = VADProcessor(vad_analyzer=endpointing_vad())
    turn = UserTurnProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            # Nothing between the microphone and the VAD: barge-in is the one
            # decision the patient pays for in real time.
            vad,
            stt,
            # Before the context, before the model. This is the ordering the
            # whole in-the-media-path argument buys.
            SafetyGate(
                protocol,
                writer,
                on_blocked=on_blocked,
                on_turn=machine.note_turn,
                on_urgent=machine.note_urgent,
            ),
            # After the gate, so a blocked transcript never ends a turn and so
            # never reaches the model.
            turn,
            user,
            llm,
            # Lifts `message_next` out of the tool call's arguments and pushes
            # it as an ordinary response — after `dispatch` has written the
            # record and ruled on it, which is the whole ordering.
            NextMessage(),
            # Between the model and the TTS: it sees the response frames and
            # hangs up after the last answer has been spoken — or after the
            # sentence a blocking concern left it to say.
            ending,
            tts,
            transport.output(),
            assistant,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # RTVI is Pipecat's own client protocol. We do not use it — there is no
        # LiveKit transport for `@pipecat-ai/client-js`, so `frontend/call`
        # speaks the wire protocol in `shared/contracts/wire.py` instead. Left
        # on, RTVI publishes hundreds of its own messages onto the same data
        # channel the browser is reading, which the client would have to parse
        # and discard.
        enable_rtvi=False,
        observers=[WireObserver(wire), SessionLogObserver(writer)],
        # The bot's only handle on storage, closed over one patient (§8).
        app_resources=writer,
    )

    return Bot(worker=worker, transport=transport, wire=wire, machine=machine)
