"""Tool dispatch and the permission matrix.

The matrix is checked in our process, before dispatch, on a call the model
cannot route around. Every refusal is still *recorded* — the audit trail shows
the attempt as well as the outcome — and every refusal still returns a result,
because a dangling tool call corrupts the next turn's context.
"""

import json

from services.agent.config.protocol import WARMUP_V1
from services.agent.machine import InterviewMachine
from services.agent.session_log import StateTransition, ToolCalled
from services.agent.tools import dispatch


class RecordingWriter:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


class RecordingWire:
    def __init__(self):
        self.notes_calls = []

    async def notes(self, fields):
        self.notes_calls.append(fields)


def setup():
    return InterviewMachine(WARMUP_V1), RecordingWriter(), RecordingWire()


async def call(machine, writer, wire, **kwargs):
    """One tool call with a patient turn behind it, which is the only kind a
    real call makes: `SafetyGate` counts the turn before either model sees the
    transcript it was captured from."""
    if kwargs.pop("spoke", True):
        machine.note_turn()
    return await dispatch(
        machine=machine, writer=writer, wire=wire,
        tool_name=kwargs.pop("tool_name", "update_intake"),
        arguments=kwargs.pop("arguments", '{"field": "day_mood", "value": "pretty good"}'),
    )


async def run_to_the_end(machine, writer, wire):
    """Both of the warm-up's questions answered: its own, and the closing one
    every protocol carries. Only the second one completes the interview."""
    await call(machine, writer, wire)
    await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "no, nothing"}',
    )


#: What `machine.captured` looks like before a single field has landed.
NOTHING_CAPTURED = {"day_mood": None, "anything_else": None}


async def test_an_authorised_call_records_the_field():
    machine, writer, wire = setup()
    result = await call(machine, writer, wire)

    assert result == {"ok": True, "recorded": "day_mood"}
    assert machine.captured == {"day_mood": "pretty good", "anything_else": None}
    assert [e for e in writer.events if isinstance(e, ToolCalled)][0].authorised is True


async def test_the_notes_card_repaints_the_moment_the_field_lands():
    """The patient is watching the record being written."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)
    assert len(wire.notes_calls) == 1
    assert wire.notes_calls[0][0].status == "captured"


async def test_capturing_the_last_field_completes_the_interview():
    """A question is done once its field is captured. Nothing else advances.

    The last field is `anything_else`, not the one the protocol authored — the
    call is not over until the patient has been asked what *they* want to raise
    and has answered.
    """
    machine, writer, wire = setup()
    await call(machine, writer, wire)
    assert machine.complete is False

    await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "no, nothing"}',
    )
    assert machine.complete is True

    transitions = [e for e in writer.events if isinstance(e, StateTransition)]
    assert [t.model_dump(by_alias=True)["from"] for t in transitions] == ["s1.q1", "close.q1"]
    assert [t.to for t in transitions] == ["close.q1", "complete"]


async def test_a_tool_the_protocol_never_declared_is_refused():
    machine, writer, wire = setup()
    result = await call(machine, writer, wire, tool_name="exfiltrate_record")

    assert result["ok"] is False
    assert "not in protocol" in result["error"]
    assert machine.captured == NOTHING_CAPTURED


async def test_a_field_the_protocol_never_declared_is_refused():
    machine, writer, wire = setup()
    result = await call(
        machine, writer, wire, arguments='{"field": "nhs_number", "value": "123"}'
    )
    assert result["ok"] is False
    assert machine.captured == NOTHING_CAPTURED


async def test_malformed_arguments_are_refused_rather_than_guessed_at():
    machine, writer, wire = setup()
    result = await call(machine, writer, wire, arguments="{not json")
    assert result["ok"] is False
    assert "not valid JSON" in result["error"]


async def test_arguments_already_parsed_by_the_framework_are_accepted():
    """Models emit a JSON string; Pipecat sometimes parses it first."""
    machine, writer, wire = setup()
    result = await call(
        machine, writer, wire, arguments={"field": "day_mood", "value": "tired"}
    )
    assert result == {"ok": True, "recorded": "day_mood"}


async def test_a_call_after_the_interview_is_complete_is_refused():
    machine, writer, wire = setup()
    await run_to_the_end(machine, writer, wire)
    result = await call(machine, writer, wire, arguments='{"field":"day_mood","value":"again"}')

    assert result["ok"] is False
    assert "complete" in result["error"]


async def test_a_capture_with_no_patient_turn_behind_it_is_refused():
    """The model, re-run on a context it has already answered, has no new
    utterance to draw on — so it reuses the last one against the next field, or
    invents one. Both happened on `iv_eca23eefda25`, and the invented answer to
    the closing question completed the interview and hung up on a patient
    mid-question.

    `run_llm=False` in `pipeline.py` is what stops the re-run happening. This is
    the rule that holds if it ever does.
    """
    machine, writer, wire = setup()
    result = await call(machine, writer, wire, spoke=False)

    assert result["ok"] is False
    assert "no patient turn" in result["error"]
    assert machine.captured == NOTHING_CAPTURED
    assert machine.current.id == "s1.q1", "a stray call must not walk the machine forward"

    refusal = [e for e in writer.events if isinstance(e, ToolCalled)][0]
    assert refusal.authorised is False and refusal.reason


async def test_one_turn_records_one_field():
    """The second call on the same turn is the one that was wrong. It is refused
    whatever it names — the previous field, or the one the machine just moved
    to."""
    machine, writer, wire = setup()
    assert (await call(machine, writer, wire))["ok"] is True

    second = await dispatch(
        machine=machine, writer=writer, wire=wire, tool_name="update_intake",
        # What the re-run actually produced on `iv_eca23eefda25`, having nothing
        # left to reuse: not an answer, and not something a patient could say.
        arguments={"field": "anything_else", "value": "The answer:\u00a0 \n\n…\u00a0\n\n…"},
    )
    assert second["ok"] is False
    assert machine.captured["anything_else"] is None
    assert machine.complete is False, "the closing question is still the patient's to answer"


async def test_the_next_turn_lifts_the_refusal():
    """The rule is one field per turn, not one field per call. A patient who
    speaks again is a patient with something new on the record."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)
    result = await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "no, nothing"}',
    )
    assert result == {"ok": True, "recorded": "anything_else"}
    assert machine.complete is True


async def test_every_refusal_is_recorded_and_answered():
    """The model is entitled to see what its call actually did, and the audit
    trail is entitled to see that it tried."""
    machine, writer, wire = setup()
    for args in ["{not json", '{"field":"nhs_number","value":"x"}', '{"field":1,"value":2}']:
        result = await call(machine, writer, wire, arguments=args)
        assert result["ok"] is False and result["error"]

    refusals = [e for e in writer.events if isinstance(e, ToolCalled) and not e.authorised]
    assert len(refusals) == 3
    assert all(r.reason for r in refusals)


# ─── the closing question cannot answer itself early ─────────────────────────


async def test_a_field_ahead_of_the_cursor_is_refused():
    """The matrix constrains the state a call is made from, not the key it
    names, so until `machine.reached` existed every question in the script was
    answerable from the first turn."""
    machine, writer, wire = setup()
    result = await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "I have not fasted"}',
    )

    assert result == {"ok": False, "error": "field is ahead of the question being asked"}
    assert machine.captured["anything_else"] is None
    refusal = [e for e in writer.events if isinstance(e, ToolCalled)][-1]
    assert refusal.authorised is False, "the attempt belongs on the audit trail"


async def test_a_field_behind_the_cursor_is_still_writable():
    """A patient correcting an earlier answer is recording the question that was
    asked, late. Only the ones ahead are answers to questions nobody has put."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)
    assert machine.current.question.field_key == "anything_else"

    result = await call(
        machine, writer, wire,
        arguments='{"field": "day_mood", "value": "actually, not great"}',
    )
    assert result == {"ok": True, "recorded": "day_mood"}
    assert machine.captured["day_mood"] == "actually, not great"


async def test_iv_44d21cb7269d_the_call_that_said_goodbye_and_kept_listening():
    """The whole failure, replayed.

    Asked how her day was going, the patient said she had not fasted. The
    tool call filed it under `anything_else` — the *closing* question — and
    the write was authorised, so the last field was full before the question was
    asked. `day_mood` landed on the next turn and the interview advanced to the
    closing question with nothing left for it to record.

    Nothing then advanced it again. `_advance` fires on a capture, there was no
    capture to make, and `machine.complete` is what `EndOfInterview` hangs up
    on — so the assistant said *"Take care, goodbye"* and went on listening
    until the patient gave up and left. The interview is filed `abandoned`.

    What makes it a machine bug rather than a model one is that no reply from
    the model could have got out of it: the closing question was answered and
    unanswerable at the same time.
    """
    machine, writer, wire = setup()

    # Turn one, on `day_mood`, answered with something about the fasting.
    await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "I have not fasted as required"}',
    )
    assert machine.captured["anything_else"] is None, (
        "the closing question must not be answered before it is asked"
    )

    # Turn two: the day_mood answer, and the interview moves to the close.
    await call(machine, writer, wire, arguments='{"field": "day_mood", "value": "Good."}')
    assert machine.current.question.field_key == "anything_else"
    assert not machine.complete

    # The closing question, asked and answered — the turn that had nothing left
    # to record on the real call.
    result = await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "the fasting issue I mentioned"}',
    )

    assert result == {"ok": True, "recorded": "anything_else"}
    assert machine.complete is True, (
        "the interview must be able to finish, which is what hangs up the call"
    )


# ─── a yes is not a thing they said ──────────────────────────────────────────


async def test_iv_53ff71f5e583_yes_is_not_an_answer_to_the_closing_question():
    """The second call this cost.

    Asked *"before we finish, is there anything else you'd like to talk about?"*
    the patient said "Yes." — and the model recorded it. `anything_else = "Yes"`
    is the last field, so the interview completed and the line closed on a
    patient who had just said she had something to raise. The record does not
    hold what it was, and nothing else does either.

    The question is grammatically answerable with yes, which is why no prompt
    can be relied on to see it. `Question.expects_content` says so out loud and
    `dispatch` holds it.
    """
    machine, writer, wire = setup()
    await call(machine, writer, wire)
    assert machine.current.question.field_key == "anything_else"

    result = await call(
        machine, writer, wire, arguments='{"field": "anything_else", "value": "Yes"}'
    )

    assert result["ok"] is False
    assert "not what it is" in result["error"]
    assert machine.captured["anything_else"] is None
    assert not machine.complete, "the patient has not said their piece yet"

    # And then they do.
    told = await call(
        machine, writer, wire,
        arguments='{"field": "anything_else", "value": "redness around the wound"}',
    )
    assert told == {"ok": True, "recorded": "anything_else"}
    assert machine.complete


async def test_an_answer_with_anything_in_it_is_never_thin():
    """Whole-string match only. The failure mode to avoid is refusing a real
    answer because it opens with the word yes."""
    for value in [
        "yes, there's redness around the wound",
        "yes I've been getting headaches",
        "no",
        "nothing, thanks",
        "yes and no",
    ]:
        machine, writer, wire = setup()
        await call(machine, writer, wire)
        result = await call(
            machine, writer, wire,
            arguments=json.dumps({"field": "anything_else", "value": value}),
        )
        assert result["ok"] is True, f"{value!r} is an answer and must land"


async def test_the_push_back_happens_once_and_then_takes_what_it_is_given():
    """The bound that stops a refusal holding the call open. It is the same rule
    the script already authors: *accept whatever they say, including nothing*."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)

    first = await call(
        machine, writer, wire, arguments='{"field": "anything_else", "value": "yeah"}'
    )
    second = await call(
        machine, writer, wire, arguments='{"field": "anything_else", "value": "yeah"}'
    )

    assert first["ok"] is False
    assert second == {"ok": True, "recorded": "anything_else"}
    assert machine.complete, "asked twice, the interview moves on"


async def test_a_question_that_wants_a_yes_still_gets_one():
    """`expects_content` is declared per question, and most are not. "Have you
    got someone to take you home?" is answered by yes, and refusing that would
    be the same bug pointed the other way."""
    from services.agent.config.protocol import PREOP_CHECK_V2

    machine, writer = InterviewMachine(PREOP_CHECK_V2), RecordingWriter()
    machine.note_turn()
    await dispatch(
        machine=machine, writer=writer, wire=None, tool_name="update_intake",
        arguments={"field": "attendance", "value": "yes", "answer": "confirmed",
                   "flag": "none"},
    )
    assert machine.captured["attendance"] == "yes"
    assert machine.current.question.field_key == "escort_home"

    machine.note_turn()
    result = await dispatch(
        machine=machine, writer=writer, wire=None, tool_name="update_intake",
        arguments={"field": "escort_home", "value": "yes", "flag": "none"},
    )
    assert result == {"ok": True, "recorded": "escort_home"}


async def test_re_sending_the_same_word_does_not_spend_the_allowance():
    """The allowance is one patient turn, not one call. A model that answers a
    refusal by re-sending "yes" has not asked anybody anything, and letting that
    through would record the thin answer with the patient never having heard the
    question a second time."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)

    first = await call(
        machine, writer, wire, arguments='{"field": "anything_else", "value": "yes"}'
    )
    # Same turn — `spoke=False`, exactly as the single-pass re-run reaches it.
    again = await call(
        machine, writer, wire, spoke=False,
        arguments='{"field": "anything_else", "value": "yes"}',
    )

    assert first["ok"] is False
    assert again["ok"] is False, "the patient has still not been asked twice"
    assert machine.captured["anything_else"] is None
