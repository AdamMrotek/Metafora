"""`docs/example-interview.md`, executed.

`WARMUP_V1` has one question and one flag, so a whole class of behaviour is
unreachable through it: nothing crosses a section boundary, no turn hits two
flags at once, and the only action that can fire is the one that stops the
call. `PREOP_CHECK_V1` is the first protocol where the compile step, the
ranking in `SEVERITY` and the tool matrix have anything to do.

The last three tests are characterisations of gaps the doc names, not
assertions that today's behaviour is right. They are written so that
implementing the feature breaks them.
"""

import pytest

from services.agent.config.protocol import PREOP_CHECK_V1 as PREOP
from services.agent.machine import InterviewMachine
from services.agent.safety import scan
from services.agent.tools import dispatch

STATES = ["s1.q1", "s1.q2", "s2.q1", "s2.q2", "s2.q3"]
FIELDS = ["attendance", "escort_home", "fasting_ack", "meds_stopped", "health_change"]


class RecordingWriter:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


async def record(machine, field, value):
    """One authorised `update_intake`, through the real dispatch path."""
    return await dispatch(
        machine=machine,
        writer=RecordingWriter(),
        wire=None,
        tool_name="update_intake",
        arguments={"field": field, "value": value},
    )


# ─── the compile step ────────────────────────────────────────────────────────


def test_two_sections_compile_into_one_ordered_run_of_states():
    m = InterviewMachine(PREOP)
    assert [s.id for s in m.states] == STATES
    assert [s.section_title for s in m.states] == [
        "Getting there",
        "Getting there",
        "Before you come in",
        "Before you come in",
        "Before you come in",
    ]


def test_the_notes_card_spans_both_sections_in_script_order():
    m = InterviewMachine(PREOP)
    fields = m.fields()
    assert [f.key for f in fields] == FIELDS
    assert [f.status for f in fields] == ["live", "pending", "pending", "pending", "pending"]


def test_the_tool_matrix_names_every_state_the_interview_can_be_in():
    """A state the matrix forgets is a question whose answer cannot be recorded,
    which is a question the interview can never advance past."""
    m = InterviewMachine(PREOP)
    allowed = {s for spec in PREOP.tools for s in spec.allowed_states}
    assert {s.id for s in m.states} <= allowed

    for _ in m.states:
        assert m.authorise("update_intake").authorised is True, f"refused at {m.current.id}"
        m.advance()


def test_tool_schema_only_ever_offers_declared_field_keys():
    m = InterviewMachine(PREOP)
    assert m.tool_definitions()[0]["parameters"]["properties"]["field"]["enum"] == FIELDS


def test_every_report_field_is_a_field_the_script_actually_captures():
    """The review composer renders `report.fields`; a key no question writes is
    a row that is always blank."""
    assert set(PREOP.report.fields) <= {
        q.field_key for s in PREOP.script.sections for q in s.questions
    }


# ─── the gate, as the studio authored its tests ──────────────────────────────


@pytest.mark.parametrize(
    "utterance,expected,action,blocked",
    [
        ("I might have to rearrange", {"yf_attendance_risk"}, "soft_review", False),
        (
            "the GP started me on antibiotics",
            {"rf_fitness_change"},
            "urgent_escalate",
            False,
        ),
        (
            "I'm still taking the apixaban",
            {"rf_anticoagulant_taken"},
            "urgent_escalate",
            False,
        ),
        ("yes, all sorted, seven o'clock", set(), None, False),
        (
            "sometimes I feel like I want to die",
            {"rf_self_harm"},
            "end_call",
            True,
        ),
    ],
)
def test_the_authored_test_table(utterance, expected, action, blocked):
    result = scan(utterance, PREOP)
    assert {h.flag.id for h in result.hits} == expected
    assert result.action == action
    assert result.blocked is blocked
    # Only `end_call` blocks, and only `end_call` carries a `say` — which is why
    # the patient is never shown a flag.
    assert (result.say is not None) is blocked


def test_only_the_self_harm_flag_stops_the_call():
    """Everything else in this protocol is administrative. An empty theatre slot
    is not a reason to hang up on somebody."""
    stopping = [f.id for f in PREOP.red_flags if f.action == "end_call"]
    assert stopping == ["rf_self_harm"]


def test_a_turn_that_hits_two_flags_is_ranked_and_keeps_both_on_the_record():
    """The doc's last turn. `SEVERITY` picks the action for the turn; the
    note-only hit still has to survive onto the record, because it is the thing
    that tells whoever rings back how to open the call."""
    result = scan(
        "I've had a chesty cough since the weekend and the GP started me on "
        "antibiotics on Monday. I'm a bit nervous about the anaesthetic.",
        PREOP,
    )
    assert {h.flag.id for h in result.hits} == {"rf_fitness_change", "nf_anxiety"}
    assert result.action == "urgent_escalate"
    assert result.blocked is False


# ─── the call in the doc, end to end ─────────────────────────────────────────

#: Ruth's five turns: what she says, what the gate makes of it, and the field
#: the capture pass records off the back of it.
CALL = [
    (
        "I think so. My mum's been poorly, so I might have to rearrange — "
        "I'm not certain yet.",
        {"yf_attendance_risk"},
        "soft_review",
        "attendance",
    ),
    ("Yes, my daughter's booked the day off.", set(), None, "escort_home"),
    ("Clear. Water till six.", set(), None, "fasting_ack"),
    ("Yes — Sunday night was the last one.", set(), None, "meds_stopped"),
    (
        "Well, I've had a chesty cough since the weekend and the GP started me "
        "on antibiotics on Monday. I'm a bit nervous about the anaesthetic with "
        "all that, to be honest.",
        {"rf_fitness_change", "nf_anxiety"},
        "urgent_escalate",
        "health_change",
    ),
]


async def test_the_call_completes_and_is_also_an_escalation():
    """Five fields for five questions, no turn withheld from the model, and a
    decision owed by a human today. The three dashboard states assume "urgent"
    means the call stopped; this one ran to the end."""
    machine = InterviewMachine(PREOP)
    actions = []

    for utterance, expected, action, field in CALL:
        assert machine.current.id == STATES[len(actions)]

        result = scan(utterance, PREOP)
        assert {h.flag.id for h in result.hits} == expected, utterance
        assert result.action == action
        assert result.blocked is False, "no turn in this call is withheld from the model"
        actions.append(action)

        assert (await record(machine, field, utterance))["ok"] is True

    assert machine.complete is True
    assert list(machine.captured) == FIELDS
    assert all(machine.captured.values())
    assert "urgent_escalate" in actions and "soft_review" in actions


# ─── gaps this protocol is the first to reach ────────────────────────────────


async def test_an_enum_field_records_the_utterance_not_the_enum_member():
    """Gap: nothing reads `Question.capture`. `machine.capture` takes a string
    and the tool schema declares `value` as one, so `attendance` lands as what
    Ruth said rather than `at_risk`.

    When enum capture is implemented this test should start failing.
    """
    question = PREOP.script.sections[0].questions[0]
    assert question.capture.type == "enum"
    assert question.capture.values == ["confirmed", "at_risk", "cannot_attend"]

    machine = InterviewMachine(PREOP)
    await record(machine, "attendance", "I think so, but I might have to rearrange")
    assert machine.captured["attendance"] == "I think so, but I might have to rearrange"


async def test_the_field_enum_is_not_scoped_to_the_current_state():
    """Gap: the matrix constrains the *state* a tool may be called from, not the
    field it may write. Every key is offered from every state, so the capture
    pass can legally record a question that has not been asked — and because
    `_advance` only fires on the current question's field, the interview stays
    where it was.

    Unreachable with a one-question protocol; live from the first call of this
    one.
    """
    machine = InterviewMachine(PREOP)
    result = await record(machine, "health_change", "no, nothing's changed")

    assert result == {"ok": True, "recorded": "health_change"}
    assert machine.current.id == "s1.q1", "recording ahead must not advance the interview"
    assert machine.fields()[4].status == "captured"


def test_an_escalation_has_no_sentence_of_its_own_to_speak():
    """Gap: `say` is only spoken when the turn is blocked (`safety.py`,
    `gate.py`), so the acknowledgement Ruth gets for a red flag that does not
    end the call is whatever the model happened to write — not scripted, not
    reviewable.
    """
    escalating = [f for f in PREOP.red_flags if f.action == "urgent_escalate"]
    assert escalating and all(f.say is None for f in escalating)

    result = scan("the GP started me on antibiotics on Monday", PREOP)
    assert result.action == "urgent_escalate"
    assert result.say is None
