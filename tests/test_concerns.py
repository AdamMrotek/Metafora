"""The second net: a flag authored on a question, resolved against its answer.

`test_safety.py` and `test_gate.py` cover the first one — a phrase, matched
before generation, unbypassable. This file is about what that net cannot see:
*"No."* to *are you still able to come?*, which carries no phrase at all.

Three properties, and they are the ones that keep the addition honest:

  · a lookup and a judgement raise the same flag and are told apart on the
    record, never in what the flag can do;
  · a model cannot raise a concern the question does not declare, and the
    attempt is filed;
  · the gate is untouched — nothing here can lower, clear or pre-empt a hit.
"""

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from services.agent import concerns
from services.agent.config.protocol import (
    PREOP_CHECK_V2,
    PREOP_SHORT_V2,
    WARMUP_V1,
)
from services.agent.end_call import EndOfInterview, _HoldOver
from services.agent.machine import InterviewMachine
from services.agent.tools import dispatch

STATES = ["s1.q1", "s1.q2", "s2.q1", "s2.q2", "s2.q3", "close.q1"]


class RecordingWriter:
    def __init__(self):
        self.events = []
        self.ending_reason = None

    def append(self, event):
        self.events.append(event)

    def note_end_reason(self, reason):
        self.ending_reason = reason


class Verdicts:
    """Every verdict `on_concern` was handed.

    It is handed one on *every* authorised capture, not only the blocking ones —
    a reply held for a question that can stop the call is waiting on it, and a
    hold released only by a concern would never be released by a clean answer.
    """

    def __init__(self):
        self.results = []

    async def __call__(self, result):
        self.results.append(result)

    @property
    def blocked(self):
        return [r for r in self.results if r.blocked]


async def record(machine, writer, field, value, *, answer=None, flag=None, stop=None):
    """One `update_intake` through the real dispatch path, turn and all."""
    machine.note_turn()
    args = {"field": field, "value": value}
    if answer is not None:
        args["answer"] = answer
    if flag is not None:
        args["flag"] = flag
    return await dispatch(
        machine=machine,
        writer=writer,
        wire=None,
        tool_name="update_intake",
        arguments=args,
        on_concern=stop,
    )


def raised(writer):
    return [e for e in writer.events if e.type == "concern.raised"]


# ─── the two triggers ────────────────────────────────────────────────────────


def question(protocol, field_key):
    return next(
        q for s in protocol.script.sections for q in s.questions if q.field_key == field_key
    )


def test_a_declared_value_raises_without_asking_anyone():
    """The enum member is the trigger. Nothing about the flag was judged — the
    capture pass classified into a closed list and the protocol did the rest."""
    result = concerns.resolve(
        question(PREOP_CHECK_V2, "attendance"), answer="cannot_attend", named=None
    )
    assert result.ids() == ["qf_attendance_cannot"]
    assert result.ids("value") == ["qf_attendance_cannot"]
    assert result.blocked is True
    assert result.say and "rebook" in result.say


def test_the_same_flag_is_reachable_by_judgement_alone():
    """No enum member — the model read the answer and named it. Same flag, same
    id, same action: the difference is provenance and nothing else."""
    result = concerns.resolve(
        question(PREOP_CHECK_V2, "attendance"),
        answer=None,
        named="qf_attendance_cannot",
    )
    assert result.ids() == ["qf_attendance_cannot"]
    assert result.ids("judged") == ["qf_attendance_cannot"]
    assert result.action == "end_call"


def test_a_lookup_wins_over_a_judgement_on_the_same_flag():
    """Both could raise it; the record should say the stronger one did."""
    result = concerns.resolve(
        question(PREOP_CHECK_V2, "attendance"),
        answer="cannot_attend",
        named="qf_attendance_cannot",
    )
    assert result.ids("value") == ["qf_attendance_cannot"]
    assert result.ids("judged") == []


def test_a_text_question_can_only_be_judged():
    """`escort_home` declares no enum, so there is nothing to look up. This is
    the case the gate is worst at — nobody says "no escort", they say their
    sister will drop them off."""
    q = question(PREOP_CHECK_V2, "escort_home")
    assert concerns.resolve(q, answer="stopped", named=None).hits == []

    result = concerns.resolve(q, answer=None, named="qf_escort_none")
    assert result.ids("judged") == ["qf_escort_none"]
    assert result.action == "soft_review"
    assert result.blocked is False


def test_two_flags_on_one_answer_rank_by_the_worse_and_keep_both():
    """Same rule as `safety.SEVERITY` applies to a turn that hits twice — and
    deliberately the same function, so the two nets cannot disagree about which
    of two levels is worse."""
    result = concerns.resolve(
        question(PREOP_CHECK_V2, "attendance"),
        answer="cannot_attend",
        named="qf_attendance_at_risk",
    )
    assert set(result.ids()) == {"qf_attendance_cannot", "qf_attendance_at_risk"}
    assert result.action == "end_call"


def test_a_question_with_no_flags_raises_nothing_whatever_it_is_told():
    q = question(WARMUP_V1, "day_mood")
    assert q.flags == []
    assert concerns.resolve(q, answer="anything", named="qf_attendance_cannot").hits == []


# ─── through dispatch, where it is filed ─────────────────────────────────────


async def test_every_capture_is_filed_even_when_it_raises_nothing():
    """The same argument `safety.scanned` makes on a clean turn: an event only
    where something fired is a record that cannot show the question was looked
    at."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    await record(machine, writer, "attendance", "yes, all sorted", answer="confirmed",
                 flag="none")

    event = raised(writer)[0]
    assert event.hits == [] and event.action is None
    assert event.field == "attendance"
    assert event.answer == "confirmed"


async def test_a_flag_the_question_does_not_declare_is_dropped_and_recorded():
    """The tool's enum is the whole protocol's — it is compiled once, before the
    call. This is the check that makes it mean the question it was called from,
    and a refusal nobody can see is indistinguishable from an authorisation."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    stop = Verdicts()
    await record(
        machine, writer, "attendance", "yes, fine",
        answer="confirmed", flag="qf_meds_still_taking", stop=stop,
    )

    event = raised(writer)[0]
    assert event.hits == []
    assert event.ignored == "qf_meds_still_taking"
    assert stop.blocked == [], "nothing was raised, so nothing stops the call"
    assert machine.current.id == "s1.q2", "the interview moved on as normal"


async def test_an_unauthorised_capture_raises_nothing_at_all():
    """A concern is something an authorised capture raised. Without the patient
    turn behind it the tool call is refused, and a refused call must not reach
    the escalation band by naming a flag."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    result = await dispatch(
        machine=machine, writer=writer, wire=None, tool_name="update_intake",
        arguments={"field": "attendance", "value": "no", "flag": "qf_attendance_cannot"},
    )
    assert result["ok"] is False
    assert raised(writer) == []


async def test_a_blocking_concern_stops_the_interview_where_it_stood():
    """`machine.complete` must not become true off the back of the answer that
    ended the call. The record should read that the interview stopped at this
    question, because it did."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    stop = Verdicts()
    result = await record(
        machine, writer, "attendance", "no, I can't make it",
        answer="cannot_attend", flag="qf_attendance_cannot", stop=stop,
    )

    assert result == {"ok": True, "recorded": "attendance", "interview": "ending"}
    assert machine.current.id == "s1.q1"
    assert machine.complete is False
    assert [r.action for r in stop.blocked] == ["end_call"]
    assert raised(writer)[0].hits == ["qf_attendance_cannot"]
    # The field is still on the record. The call stopped; the answer stands.
    assert machine.captured["attendance"] == "no, I can't make it"


async def test_a_flagged_concern_changes_nothing_about_the_call():
    """Everything below `end_call` is filed and the interview carries on — no
    interruption, nothing said, nothing skipped."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    stop = Verdicts()
    await record(machine, writer, "attendance", "I think so, but it's tight",
                 answer="at_risk", flag="none", stop=stop)

    assert stop.blocked == []
    assert len(stop.results) == 1, "the verdict still arrives — the reply is waiting on it"
    assert machine.current.id == "s1.q2"
    assert raised(writer)[0].action == "soft_review"


async def test_the_value_recorded_is_still_the_patient_s_own_words():
    """`answer` is how the reply resolves, not what the record says it was. The
    report rules ask for their words and this is where that is kept."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    await record(machine, writer, "attendance", "yeah, no problem at all",
                 answer="confirmed", flag="none")
    assert machine.captured["attendance"] == "yeah, no problem at all"


async def test_a_concern_is_resolved_against_the_current_question_not_the_field_named():
    """`field` may name a question that has not been asked — the matrix
    constrains the state, not the key. A flag authored on a question nobody
    asked is not something this answer raised."""
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    await record(machine, writer, "meds_stopped", "still on them",
                 answer="still_taking", flag="qf_meds_still_taking")

    event = raised(writer)[0]
    assert event.field == "attendance", "the question that was actually live"
    assert event.hits == []
    assert event.ignored == "qf_meds_still_taking"


# ─── the tool schema ─────────────────────────────────────────────────────────


def schema(protocol):
    return InterviewMachine(protocol).tool_definitions()[0]["parameters"]


def test_a_protocol_that_authors_neither_gets_the_schema_it_always_had():
    """Nothing is asked of a model that has nothing to answer with."""
    params = schema(WARMUP_V1)
    assert set(params["properties"]) == {"field", "value"}
    assert params["required"] == ["field", "value"]


def test_the_answer_and_flag_enums_are_closed_by_the_protocol():
    params = schema(PREOP_CHECK_V2)
    assert params["properties"]["answer"]["enum"] == [
        "confirmed", "at_risk", "cannot_attend", "stopped", "still_taking", "unsure",
    ]
    assert params["properties"]["flag"]["enum"] == [
        "qf_attendance_cannot",
        "qf_attendance_at_risk",
        "qf_escort_none",
        "qf_fasting_not_understood",
        "qf_meds_still_taking",
        "qf_meds_unsure",
        "qf_health_change",
        "none",
    ]
    assert params["required"] == ["field", "value", "flag"]


def test_the_short_protocol_offers_only_the_flags_it_carries():
    """Its schema is compiled from its own script, so the two questions it kept
    bring their flags and nothing else does."""
    assert schema(PREOP_SHORT_V2)["properties"]["flag"]["enum"] == [
        "qf_attendance_cannot",
        "qf_attendance_at_risk",
        "qf_meds_still_taking",
        "qf_meds_unsure",
        "none",
    ]


# ─── the ending, and the sentence that has to wait for a gap ─────────────────


class FakeWire:
    def __init__(self):
        self.calls = []

    async def notes(self, fields):
        self.calls.append(("notes", len(fields)))

    async def phase(self, phase):
        self.calls.append(("phase", phase))

    async def ended(self, reason, say=None):
        self.calls.append(("ended", reason, say))


@pytest.fixture
def ending():
    """The processor with its two outward moves recorded rather than made.

    `queue_frame` is the one that matters: `stop()` hands the decision to this
    processor's own task rather than acting on the capture branch's, so the test
    delivers what it queued the way the real task would.
    """
    machine, writer, wire = InterviewMachine(PREOP_CHECK_V2), RecordingWriter(), FakeWire()
    end = EndOfInterview(machine, writer, wire)
    pushed: list = []
    queued: list = []
    interruptions: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    async def enqueue(frame, direction=FrameDirection.DOWNSTREAM, callback=None):
        queued.append(frame)

    async def interrupt():
        interruptions.append(True)

    end.push_frame = capture  # type: ignore[method-assign]
    end.queue_frame = enqueue  # type: ignore[method-assign]
    end.broadcast_interruption = interrupt  # type: ignore[method-assign]
    end.queued = queued  # type: ignore[attr-defined]
    end.interruptions = interruptions  # type: ignore[attr-defined]
    return end, pushed, writer, wire


async def deliver_queued(end):
    """Run whatever `stop()` queued, as the processor's own task would."""
    for frame in list(end.queued):  # type: ignore[attr-defined]
        end.queued.remove(frame)  # type: ignore[attr-defined]
        await end.process_frame(frame, FrameDirection.DOWNSTREAM)


def spoken(pushed):
    return [f.text for f, _ in pushed if isinstance(f, TTSSpeakFrame)]


class BlockingResult:
    say = "I'm sorry to hear that. Goodbye."
    blocked = True


BlockingVerdict = BlockingResult


async def test_the_reason_is_filed_the_moment_the_concern_lands(ending):
    """Before any of the audio unwinds, because that is the part that must
    survive however the call actually ends."""
    end, _, writer, _ = ending
    await end.stop(BlockingResult())
    assert writer.ending_reason == "safety"


async def test_the_speech_pass_is_silenced_from_the_moment_the_concern_lands(ending):
    """The sentence it is streaming answers a question the interview is no
    longer asking. It never reaches the TTS, because this sits above it."""
    end, pushed, _, _ = ending
    await end.stop(BlockingResult())

    await end.process_frame(LLMTextFrame("Do you think you'll still"), FrameDirection.DOWNSTREAM)
    await end.process_frame(LLMTextFrame(" be able to make it?"), FrameDirection.DOWNSTREAM)
    assert pushed == [], "not a word of it is forwarded"


async def test_the_closure_interrupts_what_is_already_playing(ending):
    """Swallowing only stops what has not gone yet. Whatever the TTS is already
    holding has to be flushed, and that is barge-in's mechanism — the same
    event, with the roles reversed."""
    end, pushed, _, _ = ending
    await end.stop(BlockingResult())
    assert end.interruptions == [], "raised from this processor's task, not the tool handler's"

    await deliver_queued(end)
    assert end.interruptions == [True]
    assert spoken(pushed) == ["I'm sorry to hear that. Goodbye."]


async def test_the_authored_sentence_is_the_only_thing_the_patient_hears(ending):
    """The whole point. A speech pass mid-sentence, a concern, and one sentence
    comes out — the protocol's, not the model's."""
    end, pushed, _, _ = ending
    await end.process_frame(LLMTextFrame("I hear you're not feeling up to it."),
                            FrameDirection.DOWNSTREAM)
    await end.stop(BlockingResult())
    await end.process_frame(LLMTextFrame(" Do you think you'll still be able to come?"),
                            FrameDirection.DOWNSTREAM)
    await deliver_queued(end)

    assert spoken(pushed) == ["I'm sorry to hear that. Goodbye."]
    said = [f.text for f, _ in pushed if isinstance(f, LLMTextFrame)]
    assert said == ["I hear you're not feeling up to it."], "only what had already gone"


async def test_the_call_ends_once_the_closure_has_actually_been_spoken(ending):
    """Not on the `BotStoppedSpeakingFrame` the interruption itself produces —
    that one is the old sentence being cut off, and ending there would cut off
    the closure it was raised to make room for."""
    end, pushed, _, wire = ending
    await end.stop(BlockingResult())
    await deliver_queued(end)

    # The interruption stops the bot mid-sentence.
    await end.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert not [f for f, _ in pushed if isinstance(f, EndWorkerFrame)], "too early"
    assert wire.calls == []

    # Now the closure plays, and finishes.
    await end.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await end.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    ends = [(f, d) for f, d in pushed if isinstance(f, EndWorkerFrame)]
    assert [d for _, d in ends] == [FrameDirection.UPSTREAM]
    assert ("ended", "safety", "I'm sorry to hear that. Goodbye.") in wire.calls
    assert ("phase", "ended") in wire.calls


async def test_a_second_concern_does_not_get_a_second_goodbye(ending):
    end, pushed, _, _ = ending
    await end.stop(BlockingResult())
    await end.stop(BlockingResult())
    await deliver_queued(end)
    assert len(spoken(pushed)) == 1
    assert end.interruptions == [True]


async def test_a_call_that_raises_nothing_still_ends_the_way_it_always_did(ending):
    """The complete path is untouched: every question answered, the goodbye
    said by the model, then the room closes. No interruption anywhere near it."""
    end, pushed, _, wire = ending
    machine = end._machine
    for state in list(machine.states):
        machine.note_turn()
        machine.capture(state.question.field_key, "said")
        machine.advance()
    assert machine.complete

    await end.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert ("ended", "complete", None) in wire.calls
    assert [f for f, _ in pushed if isinstance(f, EndWorkerFrame)]
    assert end.interruptions == []


# ─── the hold: the reply waits for the other pass, on one kind of question ───
#
# Both passes run on the same turn, so which finishes first is a race. It only
# matters on a question whose flags include an `end_call`, and there it matters
# entirely: the speech pass is answering as though the interview continues while
# the capture pass is deciding that it does not. Measured, they normally finish
# within ~700 ms of each other; on `iv_5abb66a97374` the capture pass was 8.4 s
# late and a sentence got out. See `tuning.CONCERN_HOLD_MS`.


async def begin_reply(end):
    await end.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)


async def test_the_reply_waits_on_a_question_that_can_stop_the_call(ending):
    end, pushed, _, _ = ending
    assert end._machine.current.id == "s1.q1", "attendance — the one with an end_call flag"

    await begin_reply(end)
    await end.process_frame(LLMTextFrame("I hear you can't make it."),
                            FrameDirection.DOWNSTREAM)
    assert [f for f, _ in pushed if isinstance(f, LLMTextFrame)] == [], "held, not spoken"


async def test_a_clean_answer_releases_it_in_order(ending):
    """The common case, and it has to cost nothing: the capture pass rules, the
    reply goes out exactly as it was written."""
    end, pushed, _, _ = ending
    await begin_reply(end)
    for text in ("I hear you.", " Were you able to stop your apixaban?"):
        await end.process_frame(LLMTextFrame(text), FrameDirection.DOWNSTREAM)

    await end.answered(concerns.ConcernResult())
    await deliver_queued(end)

    assert [f.text for f, _ in pushed if isinstance(f, LLMTextFrame)] == [
        "I hear you.",
        " Were you able to stop your apixaban?",
    ]


async def test_a_blocking_answer_means_it_is_never_released(ending):
    """The reply is not late, it is wrong — it answers a question the interview
    has stopped asking. Nothing of it is spoken, and the closure is."""
    end, pushed, _, _ = ending
    await begin_reply(end)
    await end.process_frame(LLMTextFrame("Were you able to stop your apixaban?"),
                            FrameDirection.DOWNSTREAM)

    await end.answered(BlockingVerdict())
    await deliver_queued(end)

    assert [f for f, _ in pushed if isinstance(f, LLMTextFrame)] == []
    assert spoken(pushed) == ["I'm sorry to hear that. Goodbye."]


async def test_the_wait_is_bounded_and_the_reply_goes_out_anyway(ending):
    """The outlier. Nothing here can rescue a pass that is eight seconds late —
    this is only the bound on how long the patient sits in silence first. Past
    it the call behaves exactly as it did before the hold existed."""
    end, pushed, _, _ = ending
    await begin_reply(end)
    await end.process_frame(LLMTextFrame("I hear you can't make it."),
                            FrameDirection.DOWNSTREAM)
    assert [f for f, _ in pushed if isinstance(f, LLMTextFrame)] == []

    # What `_expire` queues once its sleep is over.
    await end.queue_frame(_HoldOver())
    await deliver_queued(end)

    assert [f.text for f, _ in pushed if isinstance(f, LLMTextFrame)] == [
        "I hear you can't make it."
    ]


async def test_nothing_is_held_on_a_question_that_cannot_stop_the_call(ending):
    """Every other turn of every other question is untouched. The hold costs a
    patient time, so it is spent only where the answer could make the reply
    wrong."""
    end, pushed, _, _ = ending
    machine = end._machine
    machine.note_turn()
    machine.capture("attendance", "yes")
    machine.advance()
    assert machine.current.id == "s1.q2", "escort_home — flagged, but nothing that stops"

    await begin_reply(end)
    await end.process_frame(LLMTextFrame("And have you got someone?"),
                            FrameDirection.DOWNSTREAM)

    assert [f.text for f, _ in pushed if isinstance(f, LLMTextFrame)] == [
        "And have you got someone?"
    ], "straight through, no wait"
