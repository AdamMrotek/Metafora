"""The deterministic red-flag gate.

This runs on the committed turn, *before any generation*. That ordering is
the entire point of putting the backend in the media path: the transcript
passes through our code before it reaches the model, so the scan is an
inline gate rather than a parallel observer, and it cannot be bypassed by
the model declining to tell us something.

There is no model in this file and there must never be one. A gate that
asks an LLM whether it is safe to call an LLM is not a gate.

Matching is by phrase, and it handles no negation: *"no new pain at all"*
fires a flag looking for "new pain". False positives are therefore certain
rather than unlikely, which is what decides how far a flag is allowed to
reach:

    A red flag is legitimate only when the correct response to a false
    positive is still acceptable. Stopping an admin call so a person rings
    back is. Producing clinical advice is not, at any accuracy.

That is the rule that stops the flag set growing into symptom triage. It is
also why the levels are graded by what they *ask of a human* — critical
stops the call, urgent owes a clinician a decision, flagged owes the unit
one — and not by how serious the words sounded.
"""

import re
from dataclasses import dataclass, field

from shared.contracts.models import ProtocolVersion, RedFlag, RedFlagAction


@dataclass(frozen=True)
class ScanHit:
    flag: RedFlag
    #: The matched span, kept for the audit trail.
    matched: str


@dataclass(frozen=True)
class ScanResult:
    hits: list[ScanHit] = field(default_factory=list)
    #: True when generation must not run at all.
    blocked: bool = False
    #: Set when `blocked` — spoken to the patient instead of a model reply.
    say: str | None = None
    action: RedFlagAction | None = None


#: Ranked so the most serious outcome decides the turn.
SEVERITY: dict[RedFlagAction, int] = {
    "end_call": 3,
    "urgent_escalate": 2,
    "soft_review": 1,
    "note_only": 0,
}

#: Anything that is not a letter, a digit or whitespace. `re.UNICODE` is the
#: default for `str` patterns, so `\w` here is the Unicode-aware equivalent of
#: the TypeScript `\p{L}\p{N}`.
_NON_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Normalise before matching so trivial obfuscation and ordinary speech
    artefacts do not walk past the gate: case, punctuation, and repeated
    whitespace all collapse. Padding with spaces lets patterns match at the
    string edges using the same word-boundary rule as the middle.
    """
    collapsed = _WHITESPACE.sub(" ", _NON_WORD.sub(" ", text.lower())).strip()
    return f" {collapsed} "


def scan(transcript: str, protocol: ProtocolVersion) -> ScanResult:
    haystack = normalise(transcript)
    hits: list[ScanHit] = []

    for flag in protocol.red_flags:
        for pattern in flag.patterns:
            needle = normalise(pattern)
            if needle.strip() and needle in haystack:
                hits.append(ScanHit(flag=flag, matched=pattern))
                break  # one hit per flag is enough; the flag is what matters

    if not hits:
        return ScanResult()

    worst = max(hits, key=lambda h: SEVERITY[h.flag.action])
    blocked = worst.flag.action == "end_call"

    return ScanResult(
        hits=hits,
        blocked=blocked,
        action=worst.flag.action,
        say=worst.flag.say if blocked else None,
    )
