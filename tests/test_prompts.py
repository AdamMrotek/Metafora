"""The prompt says how to talk *and* names the tool, and that is a property, not
an accident.

It was forbidden under the two-pass split, for a good reason: a pass that is
heard directly and told about a tool it does not hold can only comply by reading
the call out loud, and it did — `update_intake day_mood "..."` reached a
patient. This pass is not heard. Only its `message_next` argument is, lifted out
by `next_message.py`, which reads a field rather than a transcript, so the
syntax has nowhere to leak to.

What the split used to give for free was that the pass judging a concern was
never the pass speaking. Now it is both, so the prompt has to say so.
"""

import pytest

from services.agent.config.protocol import PROTOCOLS
from services.agent.prompts import system_prompt
from services.core.queue import synthetic_interview

INTERVIEW = synthetic_interview()

#: Has to hold for every protocol the studio can publish, not only the one the
#: queue happens to dispatch.
published = pytest.mark.parametrize(
    "protocol", list(PROTOCOLS.values()), ids=lambda p: p.id
)


def script_text(protocol) -> str:
    return " ".join(
        q.ask for s in protocol.script.sections for q in s.questions
    ).lower()


@published
def test_the_prompt_carries_both_halves(protocol):
    """One pass has to know how to speak and what to record, or the design does
    not exist."""
    prompt = system_prompt(protocol, INTERVIEW)
    assert "update_intake" in prompt
    assert "message_next" in prompt
    for word in ("out loud", "short sentences"):
        assert word in prompt, f"the prompt says nothing about {word!r}"
    for section in protocol.script.sections:
        for question in section.questions:
            assert question.field_key in prompt


@published
def test_the_prompt_forbids_speaking_a_concern(protocol):
    """The pass that judges is the pass that speaks. Under the split this could
    not happen — the speech prompt was never told a concern existed — so this
    instruction is what replaces the separation."""
    if not any(f.when for s in protocol.script.sections for q in s.questions for f in q.flags):
        pytest.skip("this protocol authors no judged concerns")
    prompt = system_prompt(protocol, INTERVIEW)
    assert "Never say anything about a concern in `message_next`" in prompt


@published
def test_the_goodbye_belongs_in_the_last_tool_call(protocol):
    """The failure that motivates the whole shape: the closing question is
    recorded and the interview completes, and if the goodbye is not in that same
    call the patient is asked a question the line is about to drop."""
    assert "The goodbye belongs in the `message_next`" in system_prompt(protocol, INTERVIEW)


@published
def test_the_prompt_does_not_restate_what_code_enforces(protocol):
    """The safety break, the escalation routes and the tool matrix are enforced
    before and around generation. Restating them as prose implies the model is
    what makes them hold, which is exactly how they stop being enforceable.

    The exemption is narrow and deliberate: a pattern may reach the prompt when
    the *question itself* says the word — `proto_preop_check_v2` asks about a
    cough, and `rf_fitness_change` matches on "cough". That is Block I doing its
    job, not Block II leaking. What must never appear is a pattern the script
    does not say, a rota name, or the sentence spoken when a call stops —
    holding both halves of the turn is not a licence to hold the third thing.
    """
    script = script_text(protocol)
    prompt = system_prompt(protocol, INTERVIEW).lower()
    for flag in protocol.red_flags:
        for pattern in flag.patterns:
            if pattern.lower() in script:
                continue
            assert pattern not in prompt, f"a red-flag pattern leaked into the prompt: {pattern!r}"
        assert flag.say is None or flag.say.lower() not in prompt
    for route in (protocol.urgent.rota if protocol.urgent else []):
        assert route not in prompt
    for question in (q for s in protocol.script.sections for q in s.questions):
        for flag in question.flags:
            assert flag.say is None or flag.say.lower() not in prompt, (
                "the sentence spoken when a call stops must not be in the prompt"
            )


@published
def test_the_prompt_carries_the_patient_and_the_clinician(protocol):
    prompt = system_prompt(protocol, INTERVIEW)
    assert INTERVIEW.patient.first_name in prompt
    assert protocol.clinician.name in prompt
    assert protocol.clinician.practice in prompt
    # Nothing per-patient beyond the first name, so the cache prefix stays
    # stable across every interview running this protocol.
    assert INTERVIEW.patient.id not in prompt
