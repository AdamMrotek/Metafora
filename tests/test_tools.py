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
    return await dispatch(
        machine=machine, writer=writer, wire=wire,
        tool_name=kwargs.pop("tool_name", "update_intake"),
        arguments=kwargs.pop("arguments", '{"field": "day_mood", "value": "pretty good"}'),
    )


async def test_an_authorised_call_records_the_field():
    machine, writer, wire = setup()
    result = await call(machine, writer, wire)

    assert result == {"ok": True, "recorded": "day_mood"}
    assert machine.captured == {"day_mood": "pretty good"}
    assert [e for e in writer.events if isinstance(e, ToolCalled)][0].authorised is True


async def test_the_notes_card_repaints_the_moment_the_field_lands():
    """The patient is watching the record being written."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)
    assert len(wire.notes_calls) == 1
    assert wire.notes_calls[0][0].status == "captured"


async def test_capturing_the_last_field_completes_the_interview():
    """A question is done once its field is captured. Nothing else advances."""
    machine, writer, wire = setup()
    await call(machine, writer, wire)

    assert machine.complete is True
    transition = [e for e in writer.events if isinstance(e, StateTransition)][0]
    assert transition.model_dump(by_alias=True)["from"] == "s1.q1"
    assert transition.to == "complete"


async def test_a_tool_the_protocol_never_declared_is_refused():
    machine, writer, wire = setup()
    result = await call(machine, writer, wire, tool_name="exfiltrate_record")

    assert result["ok"] is False
    assert "not in protocol" in result["error"]
    assert machine.captured == {"day_mood": None}


async def test_a_field_the_protocol_never_declared_is_refused():
    machine, writer, wire = setup()
    result = await call(
        machine, writer, wire, arguments='{"field": "nhs_number", "value": "123"}'
    )
    assert result["ok"] is False
    assert machine.captured == {"day_mood": None}


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
    await call(machine, writer, wire)
    result = await call(machine, writer, wire, arguments='{"field":"day_mood","value":"again"}')

    assert result["ok"] is False
    assert "complete" in result["error"]


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
