"""The prompt split is a safety property, not a style choice.

gpt-oss emits speech *or* a tool call and never both, so the two passes must be
told different things. The speech pass is never given the schema; if it is also
*told about the tool*, the only way it can comply is by reading the call out
loud — which is how `update_intake day_mood "..."` once reached a patient.
"""

from services.agent.config.protocol import WARMUP_V1
from services.agent.prompts import capture_prompt, system_prompt
from services.core.queue import next_interview

INTERVIEW = next_interview()


def test_the_spoken_prompt_never_mentions_a_tool():
    """The pass that can be heard must not know tools exist."""
    spoken = system_prompt(WARMUP_V1, INTERVIEW).lower()
    for tool in WARMUP_V1.tools:
        assert tool.name not in spoken, f"the speech prompt names {tool.name}"
    for word in ("tool", "function", "call update", "schema"):
        assert word not in spoken, f"the speech prompt mentions {word!r}"


def test_the_capture_prompt_carries_the_recording_instruction():
    """...and it lives there, with the tools, and nowhere else."""
    captured = capture_prompt(WARMUP_V1)
    assert "update_intake" in captured
    for question in WARMUP_V1.script.sections[0].questions:
        assert question.field_key in captured


def test_the_capture_prompt_says_nothing_about_how_to_speak():
    """This pass is never heard and its prose is discarded, so voice guidance
    in it is noise the model has to reconcile against a job it isn't doing."""
    captured = capture_prompt(WARMUP_V1).lower()
    for word in ("out loud", "sentences", "markdown", "interrupt", "phone call"):
        assert word not in captured, f"the capture prompt talks about speaking: {word!r}"


def test_neither_prompt_restates_what_code_enforces():
    """The safety break, the escalation routes and the tool matrix are enforced
    before and around generation. Restating them as prose implies the model is
    what makes them hold, which is exactly how they stop being enforceable."""
    both = system_prompt(WARMUP_V1, INTERVIEW).lower() + capture_prompt(WARMUP_V1).lower()
    for flag in WARMUP_V1.red_flags:
        for pattern in flag.patterns:
            assert pattern not in both, f"a red-flag pattern leaked into a prompt: {pattern!r}"
        assert flag.say is None or flag.say.lower() not in both
    for route in (WARMUP_V1.urgent.rota if WARMUP_V1.urgent else []):
        assert route not in both


def test_the_spoken_prompt_carries_the_patient_and_the_clinician():
    spoken = system_prompt(WARMUP_V1, INTERVIEW)
    assert INTERVIEW.patient.first_name in spoken
    assert WARMUP_V1.clinician.name in spoken
    assert WARMUP_V1.clinician.practice in spoken
    # Nothing per-patient beyond the first name, so the cache prefix stays
    # stable across every interview running this protocol.
    assert INTERVIEW.patient.id not in spoken
