"""The flags that hang off a question, resolved against one answer.

`safety.py` is the gate: a phrase, matched against every committed turn, before
generation, and it cannot be argued with. This is the second net. A
`QuestionFlag` is authored on the `Question` it belongs to and evaluated once —
against the answer to that question, after it — so the question supplies the
meaning the words do not carry on their own. *"No."* is a cancellation only if
you know what was asked.

Two triggers, and they are not equal:

  · **value** — the answer resolved to one of the question's own declared
    `EnumCapture` values, and a flag was waiting for that value. A table
    lookup. The model classified into a closed enum, which is a much narrower
    thing to ask than "is this concerning"; nothing here depends on its
    opinion about the flag.
  · **judged** — the model named the flag itself, against the sentence
    the author wrote in `when`. This is the half that catches metaphor, and it
    is the half that can be wrong in both directions.

A flag the current question does not declare is not a flag, whatever the model
named — the caller filters that out before it gets here, and records the
attempt. Like `safety.py` this file imports no framework and calls no model:
the judgement arrives as an argument, already made, and what is decided here is
only what the protocol does with it.
"""

from dataclasses import dataclass, field
from typing import Literal

from services.agent.safety import SEVERITY
from shared.contracts.models import Question, QuestionFlag, RedFlagAction

Trigger = Literal["value", "judged"]


@dataclass(frozen=True)
class ConcernHit:
    flag: QuestionFlag
    #: Which of the two raised it. `value` where both could have, because a
    #: lookup is the stronger claim and the record should say so.
    trigger: Trigger


@dataclass(frozen=True)
class ConcernResult:
    hits: list[ConcernHit] = field(default_factory=list)
    #: True when the worst hit stops the call.
    blocked: bool = False
    #: Set when `blocked` — the authored sentence, spoken instead of the
    #: `message_next` the model wrote for a call that is carrying on.
    say: str | None = None
    action: RedFlagAction | None = None

    def ids(self, trigger: Trigger | None = None) -> list[str]:
        return [h.flag.id for h in self.hits if trigger is None or h.trigger == trigger]


def resolve(question: Question, *, answer: str | None, named: str | None) -> ConcernResult:
    """What this answer raises, if anything.

    `answer` is the enum member the model classified into, or None for a
    question that declares no enum. `named` is the flag id it proposed, already
    checked against what this question declares.
    """
    hits: list[ConcernHit] = []

    for flag in question.flags:
        if flag.when_value is not None and answer is not None and answer == flag.when_value:
            hits.append(ConcernHit(flag=flag, trigger="value"))
        elif flag.when is not None and named == flag.id:
            hits.append(ConcernHit(flag=flag, trigger="judged"))

    if not hits:
        return ConcernResult()

    worst = max(hits, key=lambda h: SEVERITY[h.flag.action])
    blocked = worst.flag.action == "end_call"

    return ConcernResult(
        hits=hits,
        blocked=blocked,
        action=worst.flag.action,
        say=worst.flag.say if blocked else None,
    )


def declared(question: Question) -> set[str]:
    """The flag ids this question may raise. Anything else the model names is
    recorded and dropped — a protocol that did not author a concern here cannot
    have one raised on it by a model that liked the sound of it."""
    return {f.id for f in question.flags}
