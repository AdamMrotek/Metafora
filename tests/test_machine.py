"""Ported from `services/agent/src/session/machine.test.ts`.

The follow-up-budget test is deliberately not ported: `recordFollowUp` and
`ToolSpec.maxAttemptsPerTurn` were declared and tested but never wired into
`loop.ts`, so the budget existed only as prose in the system prompt ("ask once
more"). Porting the method would have carried dead logic across the migration.
The `maxAttemptsPerTurn` *field* survives on the contract, because the studio
publishes into it.
"""

import pytest

from services.agent.config.protocol import WARMUP_V1
from services.agent.machine import InterviewMachine


def test_compiles_the_script_into_fully_qualified_states():
    m = InterviewMachine(WARMUP_V1)
    # `close.q1` is not authored into this protocol: every protocol carries the
    # same closing question, so the warm-up compiles to two states, not one.
    assert [s.id for s in m.states] == ["s1.q1", "close.q1"]


def test_authorises_a_tool_only_from_a_state_the_matrix_names():
    m = InterviewMachine(WARMUP_V1)
    assert m.authorise("update_intake").authorised is True

    unknown = m.authorise("exfiltrate_record")
    assert unknown.authorised is False
    assert "not in protocol" in unknown.reason


def test_refuses_tools_once_the_interview_is_complete():
    m = InterviewMachine(WARMUP_V1)
    m.advance()
    assert m.complete is False, "the closing question is still to be asked"
    m.advance()
    assert m.complete is True
    assert m.authorise("update_intake").authorised is False


def test_refuses_to_capture_a_field_the_protocol_never_declared():
    m = InterviewMachine(WARMUP_V1)
    assert m.capture("day_mood", "pretty good") is True
    assert m.capture("nhs_number", "123 456 7890") is False
    assert m.captured == {"day_mood": "pretty good", "anything_else": None}


def test_field_states_drive_the_notes_card():
    m = InterviewMachine(WARMUP_V1)
    assert [f.model_dump() for f in m.fields()] == [
        {"key": "day_mood", "label": "How the day is going", "value": None, "status": "live"},
        {
            "key": "anything_else",
            "label": "Anything else raised",
            "value": None,
            "status": "pending",
        },
    ]

    m.capture("day_mood", "a bit tired")
    assert m.fields()[0].status == "captured"
    assert m.fields()[0].value == "a bit tired"


def test_an_unanswered_question_left_behind_is_marked_open_not_captured():
    m = InterviewMachine(WARMUP_V1)
    m.advance()
    assert m.fields()[0].status == "open"


def test_tool_schema_only_ever_offers_declared_field_keys():
    m = InterviewMachine(WARMUP_V1)
    definition = m.tool_definitions()[0]
    assert definition["parameters"]["properties"]["field"]["enum"] == [
        "day_mood",
        "anything_else",
    ]


def test_a_protocol_with_no_questions_is_rejected_at_compile_time():
    empty = WARMUP_V1.model_copy(
        update={"script": WARMUP_V1.script.model_copy(update={"sections": []})}
    )
    with pytest.raises(ValueError, match="no questions"):
        InterviewMachine(empty)
