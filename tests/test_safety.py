"""Ported from `services/agent/src/safety/scan.test.ts`.

This is one of the two suites whose logic is the product rather than the
plumbing, so it ports rather than retires with the implementation.
"""

from services.agent.config.protocol import WARMUP_V1
from services.agent.safety import ScanResult, scan


def test_every_red_flag_fires_on_its_own_proving_utterance():
    """The studio spec requires one fixture per catalog entry, generated from the
    utterance that proves the flag fires. This test derives itself from the
    protocol, so a flag added without a proving utterance fails here rather than
    shipping unproven.
    """
    assert WARMUP_V1.red_flags, "the catalog must not be empty"

    for flag in WARMUP_V1.red_flags:
        assert flag.proving_utterance, f"{flag.id} has no proving utterance"
        result = scan(flag.proving_utterance, WARMUP_V1)
        assert any(h.flag.id == flag.id for h in result.hits), (
            f'{flag.id} did not fire on: "{flag.proving_utterance}"'
        )


def test_every_end_call_flag_carries_the_sentence_spoken_in_its_place():
    for flag in (f for f in WARMUP_V1.red_flags if f.action == "end_call"):
        assert flag.say, f"{flag.id} ends the call but says nothing"


def test_an_ordinary_answer_does_not_trip_the_gate():
    for clean in [
        "my day is going well thanks",
        "a bit tired but otherwise fine",
        "I take ramipril 5 mg in the morning",
        "",
    ]:
        # Exactly the empty result: no action, no say. Not merely "not blocked".
        assert scan(clean, WARMUP_V1) == ScanResult(), f'tripped on: "{clean}"'


def test_matching_survives_casing_and_punctuation():
    r = scan("Honestly? I WANT TO DIE, some days.", WARMUP_V1)
    assert r.blocked is True
    assert r.action == "end_call"
    assert r.say, "a blocked turn must have something to say"


def test_a_blocked_turn_reports_the_sentence_instead_of_leaving_it_to_a_model():
    r = scan("sometimes I think I should end my life", WARMUP_V1)
    assert r.blocked is True
    assert r.say


def test_word_boundaries_are_respected_at_the_edges_of_the_transcript():
    # "die" alone is not a pattern; "want to die" is. A substring of a longer
    # word must not match either.
    assert scan("the diet is going well", WARMUP_V1).hits == []


def test_the_gate_reaches_no_model():
    """Structural, not behavioural: `safety.py` must never grow an inference call.

    A gate that asks an LLM whether it is safe to call an LLM is not a gate.
    Checked against the parsed module rather than its text, so the comment
    saying so does not itself trip the test.
    """
    import ast
    import pathlib

    tree = ast.parse(
        (pathlib.Path(__file__).resolve().parents[1] / "services/agent/safety.py").read_text()
    )

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not (
        imported
        & {"groq", "openai", "pipecat", "httpx", "aiohttp", "urllib", "requests"}
    ), (
        f"safety.py reached for an inference or network module: {imported}"
    )

    # Nothing async: a gate that awaits is a gate something can race.
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Await, ast.AsyncFunctionDef))], (
        "safety.py must stay synchronous"
    )
