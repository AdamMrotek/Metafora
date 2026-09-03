"""Three issue types, and the rules that keep a catalog honest about them.

`test_safety.py` proves every flag fires on its own utterance; this proves the
*set* is coherent — that each flag is at a level, that a level the protocol
claims is a level something can raise, and that the questions which can cut a
call short are the ones the catalog says they are, each with a sentence.
"""

from typing import get_args

import pytest

from services.agent.config.protocol import (
    OFFERED,
    PREOP_CHECK_V2,
    PREOP_SHORT_V2,
    PROTOCOLS,
)
from services.agent.safety import scan
from shared.contracts.models import RedFlagAction

#: The three a flag may be authored at. `note_only` is deliberately not one.
TYPES = ("end_call", "urgent_escalate", "soft_review")

published = pytest.mark.parametrize("protocol", list(PROTOCOLS.values()), ids=lambda p: p.id)
offered = pytest.mark.parametrize("protocol", list(OFFERED.values()), ids=lambda p: p.id)


# ─── the catalog ─────────────────────────────────────────────────────────────


@published
def test_flag_ids_are_unique_within_a_version(protocol):
    """A hit is filed by id and resolved against the version. Two flags sharing
    one id means a filed hit has two answers."""
    ids = [f.id for f in protocol.red_flags]
    assert len(ids) == len(set(ids)), f"{protocol.id} repeats a flag id"


@published
def test_only_a_flag_that_stops_the_call_speaks(protocol):
    """`test_safety.py` holds the other direction — every `end_call` has a
    sentence. This is the one that matters mechanically: the gate can only speak
    by pushing a frame, and that is safe solely because nothing else is
    generating on the blocked path. A `say` on any other action would race the
    model's reply into the same TTS.
    """
    for flag in protocol.red_flags:
        if flag.action != "end_call":
            assert flag.say is None, f"{flag.id} does not stop the call but speaks"


@offered
def test_nothing_is_authored_at_a_level_that_is_not_a_level(protocol):
    """A call with no flag is review ready, which is what `note_only` said."""
    for flag in protocol.red_flags:
        assert flag.action in TYPES, f"{flag.id} is authored `{flag.action}`"


@published
def test_a_protocol_that_can_raise_an_urgent_authors_what_the_patient_is_told(protocol):
    """The level's promise is that a clinician will make contact, and the one
    place that reaches the patient is this sentence. A version that can raise
    the level and authors none tells them nothing, which is the level claiming
    something the call does not do.

    Only *can raise*: `WARMUP_V1` carries an `urgent` block and no urgent flag,
    so it owes nobody a sentence.
    """
    flags = list(protocol.red_flags) + [
        f for s in protocol.script.sections for q in s.questions for f in q.flags
    ]
    if not any(f.action == "urgent_escalate" for f in flags):
        return
    assert protocol.urgent is not None, f"{protocol.id} raises an urgent and declares no level"
    assert protocol.urgent.closing, f"{protocol.id} raises an urgent and says nothing about it"


@published
def test_what_the_patient_is_told_promises_contact_and_nothing_else(protocol):
    """The gate matches phrases and handles no negation, so this sentence is
    said on false positives too. What it costs must stay bearable: a promise
    that somebody will be in touch is; anything about what was found is not."""
    if protocol.urgent is None or not protocol.urgent.closing:
        return
    said = protocol.urgent.closing.lower()
    assert "in touch" in said or "contact" in said, "it has to actually promise contact"
    for word in ("urgent", "flag", "concern", "risk", "serious", "worried"):
        assert word not in said, f"{protocol.id} tells the patient it is `{word}`"


def test_note_only_survives_in_the_contract():
    """Not authored and deleted are different acts, and this is the first. A
    record written while `note_only` was still authored carries hits at that
    level, and a contract that drops a value the record contains cannot read its
    own history."""
    assert "note_only" in get_args(RedFlagAction)


# ─── the pre-op config, gone through ─────────────────────────────────────────


def test_the_preop_config_can_raise_every_level_it_claims():
    """A level nothing can raise is a claim the system does not keep. This is
    what makes the urgent flag mandatory rather than a nice-to-have: moving the
    booking flags to `soft_review` empties `urgent_escalate` unless something
    replaces them."""
    actions = {f.action for f in PREOP_CHECK_V2.red_flags}
    assert actions == set(TYPES)


def test_the_short_protocol_can_still_escalate():
    """Its reason for existing is a thirty-second run that reaches an
    escalation. `SELF_HARM` is not the flag anyone drives a smoke test
    through."""
    escalating = [
        f.id for f in PREOP_SHORT_V2.red_flags if f.action == "urgent_escalate"
    ]
    assert escalating == ["surgical_site_change"]


@pytest.mark.parametrize(
    "utterance,flag_id,action",
    [
        ("I'm still taking the apixaban", "rf_anticoagulant_taken", "soft_review"),
        ("the GP started me on antibiotics on Monday", "rf_fitness_change", "soft_review"),
        ("I might have to rearrange", "yf_attendance_risk", "soft_review"),
        (
            "it's gone red and hot where they're operating, and it's getting worse",
            "surgical_site_change",
            "urgent_escalate",
        ),
    ],
)
def test_v2_scans_each_answer_at_its_new_level(utterance, flag_id, action):
    result = scan(utterance, PREOP_CHECK_V2)
    assert {h.flag.id for h in result.hits} == {flag_id}
    assert result.action == action
    assert result.blocked is False


def test_the_urgent_flag_does_not_stop_the_call():
    """Urgent continues, unchanged — no interruption, nothing said at the time.
    Only critical blocks."""
    result = scan(
        "it's gone red and hot where they're operating, and it's getting worse",
        PREOP_CHECK_V2,
    )
    assert result.blocked is False
    assert result.say is None


def test_a_turn_at_two_levels_ranks_by_the_worse_and_keeps_both():
    result = scan(
        "I'm still taking the apixaban, and it's gone red and hot where they're operating",
        PREOP_CHECK_V2,
    )
    assert {h.flag.id for h in result.hits} == {
        "rf_anticoagulant_taken",
        "surgical_site_change",
    }
    assert result.action == "urgent_escalate"


def test_the_anxiety_flag_is_gone_and_nothing_replaced_it():
    assert scan("I'm a bit nervous about the anaesthetic", PREOP_CHECK_V2).hits == []


# ─── the flags that hang off a question ──────────────────────────────────────


def questions(protocol):
    return [q for s in protocol.script.sections for q in s.questions]


def question_flags(protocol):
    return [(q, f) for q in questions(protocol) for f in q.flags]


@published
def test_a_flag_id_is_unique_across_both_lists(protocol):
    """Question flags and red flags are filed the same way — an id, resolved
    against the pinned version. One id meaning two things is a filed hit with
    two answers, and it does not matter which list each half came from."""
    ids = [f.id for f in protocol.red_flags] + [f.id for _, f in question_flags(protocol)]
    assert len(ids) == len(set(ids)), f"{protocol.id} repeats a flag id"


@published
def test_only_a_question_flag_that_stops_the_call_speaks(protocol):
    """The same mechanical rule as `test_only_a_flag_that_stops_the_call_speaks`.
    A sentence on any other action races the model's own reply into the same
    TTS."""
    for _, flag in question_flags(protocol):
        if flag.action != "end_call":
            assert flag.say is None, f"{flag.id} does not stop the call but speaks"
    for _, flag in question_flags(protocol):
        if flag.action == "end_call":
            assert flag.say, f"{flag.id} stops the call and says nothing"


@offered
def test_no_question_flag_is_authored_at_a_level_that_is_not_a_level(protocol):
    for _, flag in question_flags(protocol):
        assert flag.action in TYPES, f"{flag.id} is authored `{flag.action}`"


@published
def test_a_value_trigger_names_a_value_its_question_can_actually_hold(protocol):
    """`when_value` is a table lookup against the answer, so a value the
    question's `EnumCapture` does not declare is a trigger that can never fire —
    and it fails silently, because nothing is wrong with the string itself."""
    for question, flag in question_flags(protocol):
        if flag.when_value is None:
            continue
        assert question.capture.type == "enum", (
            f"{flag.id} matches a value on `{question.field_key}`, "
            f"which captures `{question.capture.type}`"
        )
        assert flag.when_value in question.capture.values, (
            f"{flag.id} waits for `{flag.when_value}`, "
            f"which {question.field_key} never holds"
        )


@published
def test_a_text_question_can_only_be_judged(protocol):
    """The other direction of the same rule: with no enum there is no lookup, so
    the flag has to declare the condition in words or it declares nothing."""
    for question, flag in question_flags(protocol):
        if question.capture.type != "enum":
            assert flag.when, f"{flag.id} hangs off a text field with no `when`"


def test_the_deterministic_trigger_is_preferred_where_the_enum_has_one():
    """Both triggers on the flags that can have both. The lookup is the floor —
    a flag that dropped it would be asking a model something the protocol
    already knows how to answer."""
    both = {
        f.id
        for q, f in question_flags(PREOP_CHECK_V2)
        if q.capture.type == "enum"
    }
    assert both == {
        "qf_attendance_cannot",
        "qf_attendance_at_risk",
        "qf_meds_still_taking",
        "qf_meds_unsure",
    }
    for _, flag in question_flags(PREOP_CHECK_V2):
        if flag.id in both:
            assert flag.when_value is not None and flag.when is not None


def test_the_preop_questions_raise_at_three_levels():
    """Same claim as the red-flag catalog makes, one block over: a level nothing
    can raise is a level the system does not have."""
    assert {f.action for _, f in question_flags(PREOP_CHECK_V2)} == set(TYPES)


# ─── what can end a call ─────────────────────────────────────────────────────

#: The table in `protocol.py`'s docstring, as an assertion. Per protocol: the
#: gate flags that stop a call, then the field keys whose answer can. Written
#: out rather than derived, because the point is that adding a way to hang up on
#: a patient is a decision somebody makes on purpose and not a side effect of
#: authoring a flag.
ENDINGS = {
    "proto_warmup_v1": (["rf_self_harm"], []),
    "proto_preop_check_v2": (
        ["rf_self_harm"],
        ["attendance", "anything_else"],
    ),
    "proto_preop_short_v2": (
        ["rf_self_harm"],
        ["attendance", "anything_else"],
    ),
}


@published
def test_the_catalog_names_every_way_a_call_can_be_cut_short(protocol):
    """There is no hangup tool: a call ends because every field is captured, or
    because a flag authored at `end_call` stopped it. This is the second set, and
    it is small on purpose — carrying on asking has to be worse than stopping
    before a question earns the right to end the interview."""
    gate, fields = ENDINGS[protocol.id]

    assert [f.id for f in protocol.red_flags if f.action == "end_call"] == gate
    assert [
        q.field_key for q in questions(protocol) if any(f.action == "end_call" for f in q.flags)
    ] == fields


@published
def test_every_ending_says_something_before_it_hangs_up(protocol):
    """`say` is not decoration. `next_message.speakable` withholds whatever the
    model wrote the moment a capture comes back ending, so a flag with no
    sentence of its own drops the line in silence."""
    for flag in protocol.red_flags:
        if flag.action == "end_call":
            assert flag.say, f"{flag.id} stops the call and says nothing"
    for _, flag in question_flags(protocol):
        if flag.action == "end_call":
            assert flag.say, f"{flag.id} stops the call and says nothing"


def test_the_only_question_mid_script_that_stops_the_call_is_the_one_about_coming():
    """Everything else the unit authored is a thing to write down. An answer that
    means the operation is not happening is the one case where carrying on
    asking is worse than stopping — and the closing question is not mid-script:
    it ends the interview either way, and the flag only decides whose sentence
    the patient hears on the way out."""
    stopping = [
        f.id
        for q, f in question_flags(PREOP_CHECK_V2)
        if f.action == "end_call" and q.field_key != "anything_else"
    ]
    assert stopping == ["qf_attendance_cannot"]


@published
def test_the_closing_question_is_the_last_one_and_is_never_must_capture(protocol):
    """Every script ends by asking the patient what they want to raise, and
    nobody is owed an answer to it. It carries a flag now — leaving that turn to
    the gate meant a symptom said in the patient's own words matched no pattern
    and reached no clinician — but it is still their turn and not a field the
    interview can stall on."""
    last = questions(protocol)[-1]
    assert last.field_key == "anything_else"
    assert last.must_capture is False
    assert last.expects_content is True


# ─── offered is not published ────────────────────────────────────────────────


def test_every_offered_protocol_is_published():
    assert set(OFFERED) <= set(PROTOCOLS)


def test_a_superseded_version_stays_in_the_catalog_and_off_the_composer():
    """Both halves, for whenever there is a superseded version again. Dropping
    one from `PROTOCOLS` breaks the interviews pinned to it; leaving it in
    `OFFERED` lets a clinician queue a new call against a version that has been
    replaced. Nothing is superseded today, so this asserts the shape rather than
    a member — see `test_dispatch.test_a_superseded_protocol_cannot_be_dispatched`
    for the behaviour it protects."""
    for protocol_id in PROTOCOLS:
        if protocol_id not in OFFERED:
            assert PROTOCOLS[protocol_id].frozen
