"""The prompt split is a safety property, not a style choice.

gpt-oss emits speech *or* a tool call and never both, so the two passes must be
told different things. The speech pass is never given the schema; if it is also
*told about the tool*, the only way it can comply is by reading the call out
loud — which is how `update_intake day_mood "..."` once reached a patient.
"""

import pytest

from services.agent.config.protocol import PROTOCOLS
from services.agent.prompts import capture_prompt, system_prompt
from services.core.queue import next_interview

INTERVIEW = next_interview()

#: The split has to hold for every protocol the studio can publish, not only
#: the one the queue happens to dispatch.
published = pytest.mark.parametrize(
    "protocol", list(PROTOCOLS.values()), ids=lambda p: p.id
)


def script_text(protocol) -> str:
    return " ".join(
        q.ask for s in protocol.script.sections for q in s.questions
    ).lower()


@published
def test_the_spoken_prompt_never_mentions_a_tool(protocol):
    """The pass that can be heard must not know tools exist."""
    spoken = system_prompt(protocol, INTERVIEW).lower()
    for tool in protocol.tools:
        assert tool.name not in spoken, f"the speech prompt names {tool.name}"
    for word in ("tool", "function", "call update", "schema"):
        assert word not in spoken, f"the speech prompt mentions {word!r}"


@published
def test_the_capture_prompt_carries_the_recording_instruction(protocol):
    """...and it lives there, with the tools, and nowhere else."""
    captured = capture_prompt(protocol)
    assert "update_intake" in captured
    for section in protocol.script.sections:
        for question in section.questions:
            assert question.field_key in captured


@published
def test_the_capture_prompt_says_nothing_about_how_to_speak(protocol):
    """This pass is never heard and its prose is discarded, so voice guidance
    in it is noise the model has to reconcile against a job it isn't doing."""
    captured = capture_prompt(protocol).lower()
    for word in ("out loud", "sentences", "markdown", "interrupt", "phone call"):
        assert word not in captured, f"the capture prompt talks about speaking: {word!r}"


@published
def test_neither_prompt_restates_what_code_enforces(protocol):
    """The safety break, the escalation routes and the tool matrix are enforced
    before and around generation. Restating them as prose implies the model is
    what makes them hold, which is exactly how they stop being enforceable.

    The exemption is narrow and deliberate: a pattern may reach a prompt when
    the *question itself* says the word — `proto_preop_check_v1` asks about a
    cough, and `rf_fitness_change` matches on "cough". That is Block I doing its
    job, not Block II leaking. What must never appear is a pattern the script
    does not say, the sentence spoken when a call ends, or a rota name.
    """
    script = script_text(protocol)
    both = system_prompt(protocol, INTERVIEW).lower() + capture_prompt(protocol).lower()
    for flag in protocol.red_flags:
        for pattern in flag.patterns:
            if pattern.lower() in script:
                continue
            assert pattern not in both, f"a red-flag pattern leaked into a prompt: {pattern!r}"
        assert flag.say is None or flag.say.lower() not in both
    for route in (protocol.urgent.rota if protocol.urgent else []):
        assert route not in both


@published
def test_the_spoken_prompt_carries_the_patient_and_the_clinician(protocol):
    spoken = system_prompt(protocol, INTERVIEW)
    assert INTERVIEW.patient.first_name in spoken
    assert protocol.clinician.name in spoken
    assert protocol.clinician.practice in spoken
    # Nothing per-patient beyond the first name, so the cache prefix stays
    # stable across every interview running this protocol.
    assert INTERVIEW.patient.id not in spoken
