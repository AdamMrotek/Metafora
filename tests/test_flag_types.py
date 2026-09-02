"""Three issue types, and the rules that keep a catalog honest about them.

`test_safety.py` proves every flag fires on its own utterance; this proves the
*set* is coherent — that each flag is at a level, that a level the protocol
claims is a level something can raise, and that publishing v2 left v1 exactly
where it was.
"""

from typing import get_args

import pytest

from services.agent.config.protocol import (
    OFFERED,
    PREOP_CHECK_V1,
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


def test_note_only_survives_in_the_contract():
    """Not authored and deleted are different acts, and this is the first.
    Interviews filed under v1 carry `note_only` hits, and a contract that drops
    a value the record contains cannot read its own history."""
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


# ─── and v1 did not move ─────────────────────────────────────────────────────


def test_publishing_v2_left_v1_exactly_where_it_was():
    """Every hit already in `transcript.events` resolves against the version its
    interview pinned. If v1's actions move, so does the meaning of a call filed
    months ago — which is the whole reason this was a new id and not an edit."""
    by_id = {f.id: f for f in PREOP_CHECK_V1.red_flags}
    assert by_id["rf_fitness_change"].action == "urgent_escalate"
    assert by_id["rf_anticoagulant_taken"].action == "urgent_escalate"
    assert by_id["nf_anxiety"].action == "note_only"


def test_the_flags_that_moved_kept_their_ids_labels_and_patterns():
    """Only the action moved. An id is an opaque key the record has already
    filed, and the level is the action — never the `rf_` prefix, which is left
    over from an older vocabulary."""
    v1 = {f.id: f for f in PREOP_CHECK_V1.red_flags}
    v2 = {f.id: f for f in PREOP_CHECK_V2.red_flags}

    for flag_id in ("rf_fitness_change", "rf_anticoagulant_taken"):
        assert v2[flag_id].label == v1[flag_id].label
        assert v2[flag_id].patterns == v1[flag_id].patterns
        assert v2[flag_id].action != v1[flag_id].action


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
    A sentence on any other action races the speech pass into the same TTS."""
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


def test_the_only_question_that_stops_the_call_is_the_one_about_coming():
    """Everything else on this script is a thing to write down. An answer that
    means the operation is not happening is the one case where carrying on
    asking is worse than stopping."""
    stopping = [f.id for _, f in question_flags(PREOP_CHECK_V2) if f.action == "end_call"]
    assert stopping == ["qf_attendance_cannot"]


def test_the_closing_question_carries_no_flag():
    """`CLOSING` is shared by every protocol and authored by none of them. What
    a patient raises on their own turn is not an answer to a question, so there
    is nothing for a question flag to hang off — the gate watches that turn."""
    for protocol in PROTOCOLS.values():
        assert questions(protocol)[-1].field_key == "anything_else"
        assert questions(protocol)[-1].flags == []


# ─── and v1's script did not move either ─────────────────────────────────────


def test_v1_questions_carry_no_flags():
    """A question flag changes what a version does. v1 is what interviews
    already filed are read back against, so it does not grow one."""
    assert all(q.flags == [] for q in questions(PREOP_CHECK_V1))


def test_v2_changed_nothing_about_a_question_except_what_it_can_raise():
    """v2's script is written out rather than derived, so this is the guard
    against a typo silently minting a new field key or a different capture."""
    v1 = questions(PREOP_CHECK_V1)
    v2 = questions(PREOP_CHECK_V2)
    assert len(v1) == len(v2)

    for a, b in zip(v1, v2, strict=True):
        assert b.model_dump(exclude={"flags"}) == a.model_dump(exclude={"flags"})


# ─── offered is not published ────────────────────────────────────────────────


def test_every_offered_protocol_is_published():
    assert set(OFFERED) <= set(PROTOCOLS)


def test_a_superseded_version_stays_in_the_catalog_and_off_the_composer():
    """Both halves. Dropping it from `PROTOCOLS` would break the interviews
    pinned to it; leaving it in `OFFERED` would let a clinician queue a new call
    against a version that has been replaced."""
    assert PREOP_CHECK_V1.id in PROTOCOLS
    assert PREOP_CHECK_V1.id not in OFFERED
