"""Tool dispatch and the permission matrix.

The matrix is checked in our process, before dispatch, on a call the model
cannot route around. Every refusal is still *recorded* — the audit trail shows
the attempt as well as the outcome — and every refusal still returns a result,
because a dangling tool call corrupts the next turn's context.
"""

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
    """The capture pass, re-run on a context it has already answered, has no new
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
