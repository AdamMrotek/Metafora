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
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from services.agent import concerns
from services.agent.config.protocol import (
    PREOP_CHECK_V2,
    PREOP_SHORT_V2,
    PROTOCOLS,
    WARMUP_V1,
)
from services.agent.end_call import EndOfInterview
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
    model classified into a closed list and the protocol did the rest."""
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
    """`field` may still name a question other than the live one — a patient
    correcting an earlier answer is recording the question that was asked, late,
    and that is allowed. A flag authored on *that* question is not something
    this answer raised, so the live question's flags are what it is resolved
    against.

    Fields *ahead* of the cursor no longer reach here at all: `dispatch` refuses
    them, because a question that answers itself before it is asked is also a
    question that never advances the interview (`machine.reached`).
    """
    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    await record(machine, writer, "attendance", "yes, I'll be there", answer="confirmed")
    assert machine.current.question.field_key == "escort_home"

    await record(machine, writer, "attendance", "actually I can still make it",
                 answer="confirmed", flag="qf_attendance_cannot")

    event = raised(writer)[-1]
    assert event.field == "escort_home", "the question that was actually live"
    assert event.hits == []
    assert event.ignored == "qf_attendance_cannot"


# ─── the tool schema ─────────────────────────────────────────────────────────


def schema(protocol):
    return InterviewMachine(protocol).tool_definitions()[0]["parameters"]


def test_a_protocol_that_authors_neither_gets_only_what_every_turn_needs():
    """Nothing is asked of a model that has nothing to answer with. What is left
    is the turn itself: the field, the words, and what to say next."""
    params = schema(WARMUP_V1)
    assert set(params["properties"]) == {"field", "value", "message_next"}
    assert params["required"] == ["field", "value", "message_next"]


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
        # The closing question authors one now: what a patient raises on their
        # own turn is the answer to `anything_else`, and leaving it to the gate
        # meant a symptom in their own words matched no pattern.
        "qf_closing_concern",
        "none",
    ]
    assert params["required"] == ["field", "value", "flag", "message_next"]


@pytest.mark.parametrize("protocol", list(PROTOCOLS.values()), ids=lambda p: p.id)
def test_the_sentence_is_the_last_argument_and_never_optional(protocol):
    """`message_next` is on every schema, because it is the shape of the turn —
    what the patient hears is an argument of the call that records what they
    just said.

    Last, so the model writes it after it has settled what it is recording.
    Required, because an optional sentence is a silent turn whenever the model
    omits it.
    """
    params = InterviewMachine(protocol).tool_definitions()[0]["parameters"]
    assert list(params["properties"])[-1] == "message_next"
    assert params["required"][-1] == "message_next"


def test_the_short_protocol_offers_only_the_flags_it_carries():
    """Its schema is compiled from its own script, so the two questions it kept
    bring their flags and nothing else does."""
    assert schema(PREOP_SHORT_V2)["properties"]["flag"]["enum"] == [
        "qf_attendance_cannot",
        "qf_attendance_at_risk",
        "qf_meds_still_taking",
        "qf_meds_unsure",
        # Not one of the two it kept — the closing question is on every script.
        "qf_closing_concern",
        "none",
    ]


# ─── the ending, and the sentence the system says rather than the model ─────


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
    processor's own task rather than acting on the function-call task it is
    called from, so the test delivers what it queued the way the real task
    would.
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


async def test_anything_still_in_flight_is_silenced_from_the_moment_it_lands(ending):
    """Text already on its way answers a question the interview is no longer
    asking. It never reaches the TTS, because this sits above it. Usually there
    is none — `next_message.py` withheld the sentence before it was ever
    generated — so this is the backstop, not the mechanism."""
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
    """The whole point. A reply mid-sentence, a concern, and one sentence comes
    out — the protocol's, not the model's."""
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
