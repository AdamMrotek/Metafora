"""The conversation, as a Pipecat pipeline.

`loop.ts` was 623 lines because it hand-rolled endpointing, barge-in, sentence
chunking, synthesise-ahead playback, SSE parsing and WAV handling. All of that
is framework default now. What is left here is the ordering — and the ordering
is the architecture:

    transport.input  →  vad  →  stt  →  SAFETY GATE  →  turn  →  user context  →  llm
                                                                                     ↓
    transport.output  ←──  tts (trimmed, Orpheus-capped)  ←──────────────────────────┘

The transcript passes through our code before it reaches the model, so the gate
cannot be bypassed. Everything the assistant says leaves from the same process,
so the session log is a first-hand record rather than a reconstruction of
whatever the browser chose to relay.

`vad` and `turn` sit above the split for the same reason the gate does: there is
one patient, so there is one turn. Below the split each branch would decide the
turn for itself, and two branches deciding separately is two answers to a
question that has one.
"""

from dataclasses import dataclass

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
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

from services.agent.capture import SilentBranch
from services.agent.config import tuning
from services.agent.end_call import EndOfInterview
from services.agent.gate import SafetyGate
from services.agent.machine import InterviewMachine
from services.agent.observer import SessionLogObserver
from services.agent.prompts import capture_prompt, system_prompt
from services.agent.session_log import SessionWriter
from services.agent.tools import dispatch
from services.agent.tts import TrimmedGroqTTSService
from services.agent.tts_text import OrpheusAggregator
from services.agent.wire import Wire, WireObserver
from shared.contracts.models import ProtocolVersion, QueuedInterview


def endpointing_vad() -> SileroVADAnalyzer:
    """Speech detection, for the single `VADProcessor` above the split.

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
    """User-aggregator params for a branch that is *told* when the turn ended.

    Left to its defaults an aggregator decides the turn itself, which means a
    `SileroVADAnalyzer` and a `LocalSmartTurnAnalyzerV3` per aggregator — and we
    have two. `ExternalUserTurnStrategies` makes it wait to be told instead: it
    detects nothing, emits no `UserStartedSpeakingFrame`, and raises no
    interruption. `UserTurnProcessor` upstream does all three, once.

    A fresh instance per aggregator, because a strategy holds the state of the
    turn its own context is accumulating and the two contexts flush
    independently. It is the *decision* that must not be duplicated, and that
    now happens above the split.
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

    speech_llm = _llm()
    capture_llm = _llm()

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
    # decided in this branch and can only be *spoken* from the main path.
    ending = EndOfInterview(machine, writer, wire)

    async def _on_update_intake(params: FunctionCallParams) -> None:
        # The permission matrix runs in-process before anything is captured;
        # the result string is what the model sees, folded back into context.
        result = await dispatch(
            machine=machine,
            writer=writer,
            wire=wire,
            tool_name=params.function_name,
            arguments=params.arguments,
            # Every authorised capture, not only the ones that raise something:
            # the reply held for a question that can stop the call is waiting on
            # this, and a reply held for a concern that never came is a reply
            # nobody hears.
            on_concern=ending.answered,
        )
        # `run_llm=False` is the whole reason this pass stays honest. Pipecat
        # defaults a tool result to True — the aggregator re-runs inference so a
        # model can say something about what its call returned. This pass has
        # nothing to say: `SilentBranch` discards its prose, and the patient
        # hears the speech pass. What the re-run does instead is see a context
        # it has already answered, notice the question has moved on, and record
        # the previous turn against the next field — or, with nothing left to
        # reuse, invent one. On `iv_eca23eefda25` it did both, and the invented
        # answer to the closing question completed the interview and hung up on
        # a patient who had not been given the chance to answer it.
        await params.result_callback(
            result, properties=FunctionCallResultProperties(run_llm=False)
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

    # ── two passes, run at the same time ──
    #
    # The speech pass carries NO tools. That is not an oversight: gpt-oss emits
    # speech or a tool call and never both, so a pass holding the schema is a
    # pass that goes silent on exactly the turn the patient just answered.
    # Telling a tool-less pass to "call update_intake" is worse still — it can
    # only obey by reading the call out loud, and it did, to a patient.
    #
    # The two share the conversation but not their instructions, because only
    # one of them holds the tools.
    speech_context = LLMContext(
        messages=[{"role": "system", "content": system_prompt(protocol, interview)}]
    )
    speech_user, speech_assistant = LLMContextAggregatorPair(
        speech_context, user_params=told_the_turn()
    )

    capture_context = LLMContext(
        messages=[{"role": "system", "content": capture_prompt(protocol)}],
        tools=tools,
    )
    capture_user, capture_assistant = LLMContextAggregatorPair(
        capture_context, user_params=told_the_turn()
    )

    # ── one turn, decided once, above the split ──
    #
    # `TransportParams` carries no VAD in 1.7, so speech detection and turn
    # detection are processors now. Placed inside the aggregators — where the
    # defaults put them — this is two `SileroVADAnalyzer`s and two
    # `LocalSmartTurnAnalyzerV3`s: two ONNX sessions scoring every frame of the
    # patient's audio, reaching two end-of-turn verdicts that nothing
    # reconciles, and broadcasting two `InterruptionFrame`s per barge-in. The
    # turn belongs to the patient, not to whichever branch noticed it.
    #
    # `UserTurnProcessor` keeps Pipecat's defaults — VAD and transcription for
    # the start, `LocalSmartTurnAnalyzerV3` for the stop — so the behaviour is
    # the one `tuning.py` describes and `EndpointDecision` is measuring. What
    # changes is that there is now one of it.
    vad = VADProcessor(vad_analyzer=endpointing_vad())
    turn = UserTurnProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            # Nothing between the microphone and the VAD: barge-in is the one
            # decision the patient pays for in real time.
            vad,
            stt,
            # Before either context, before either model. This is the ordering
            # the whole in-the-media-path argument buys.
            SafetyGate(protocol, writer, on_blocked=on_blocked, on_turn=machine.note_turn),
            # After the gate, so a blocked transcript never ends a turn and so
            # never reaches a model. `broadcast_frame` pushes the turn frames
            # downstream into the `ParallelPipeline`, which forks them to both
            # branches — that is how one decision reaches two contexts.
            turn,
            ParallelPipeline(
                # Heard. Streams the first sentence while the other branch is
                # still deciding what to record. Note the assistant aggregator
                # is NOT in here: it consumes the LLM's text frames to build
                # context, so inside the branch it swallows the reply and the
                # TTS downstream never sees a word. It belongs at the end of
                # the pipeline, after the audio has been published.
                [speech_user, speech_llm],
                # Never heard. Writes the record. Its aggregator *does* live in
                # the branch, because the tool result has to get back into this
                # context, and everything after it is discarded anyway.
                [capture_user, capture_llm, capture_assistant, SilentBranch()],
            ),
            # Between the models and the TTS: it sees the response frames and
            # hangs up after the last answer has been spoken — or after the
            # sentence a blocking concern left it to say.
            ending,
            tts,
            transport.output(),
            speech_assistant,
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
        observers=[
            WireObserver(wire, speech_llm=speech_llm),
            SessionLogObserver(writer, speech_llm=speech_llm),
        ],
        # The bot's only handle on storage, closed over one patient (§8).
        app_resources=writer,
    )

    return Bot(worker=worker, transport=transport, wire=wire, machine=machine)
