"""Tool dispatch, with the permission matrix in front of it.

The matrix is checked here rather than described to the model, so a call from a
state the protocol does not allow is refused whatever the model decided. That is
the difference between a guarantee and a request: `machine.authorise` runs in
our process, on a call the model cannot route around.

Four refusal modes, each recorded with `authorised=False` so the audit trail
shows the attempt as well as the outcome:

  · arguments that are not the shape the schema declared
  · a call from a state the matrix does not name
  · a call with no patient turn behind it — nothing was said, so there is
    nothing to record, whatever the model has found to put in the field
  · a field key the protocol never declared
  · a field the interview has not reached — an answer to a question nobody has
    asked yet. The matrix constrains the state a call is made from, not the key
    it names, and on `iv_44d21cb7269d` a first-turn remark about fasting was
    filed under the *closing* question. That question then had nothing left to
    record, so nothing advanced the machine past it, so `complete` never came
    true and the call never hung up. See `machine.position`.
  · a bare *yes* to a question that `expects_content`. Refused once, and then
    only once — see `_is_thin`.

Every one of them still returns a result to the model. A dangling tool call
corrupts the next turn's context, and the model is entitled to see what its call
actually did.

Once the value is in, the question's own flags are resolved against it
(`concerns.py`) and the result is filed as a `concern.raised`. That happens
**after** the permission matrix and never instead of it: a concern is something
an authorised capture raised, so a call that was refused raises nothing, and a
model cannot reach the escalation band by proposing a flag on a field it was
not allowed to write.
"""

import json
from typing import Any

from services.agent import concerns
from services.agent.machine import InterviewMachine
from services.agent.session_log import (
    ConcernRaised,
    SessionWriter,
    StateTransition,
    ToolCalled,
)


async def dispatch(
    *,
    machine: InterviewMachine,
    writer: SessionWriter,
    wire: Any,
    tool_name: str,
    arguments: Any,
    on_concern: Any = None,
) -> dict[str, Any]:
    """Record one field, then work out what the answer raised.

    `on_concern` is how the verdict gets out of here. This runs on the LLM
    service's function-call task and cannot push a frame of its own, so a
    concern that stops the call is handed to `EndOfInterview.answered`, which
    queues the decision onto the pipeline's own task.
    """
    args = _coerce(arguments)
    if args is None:
        writer.append(
            ToolCalled(
                name=tool_name,
                args=repr(arguments),
                authorised=False,
                reason="arguments were not valid JSON",
            )
        )
        return {"ok": False, "error": "arguments were not valid JSON"}

    verdict = machine.authorise(tool_name)
    if not verdict.authorised:
        writer.append(
            ToolCalled(name=tool_name, args=args, authorised=False, reason=verdict.reason)
        )
        return {"ok": False, "error": verdict.reason}

    field = args.get("field")
    value = args.get("value")
    if not isinstance(field, str) or not isinstance(value, str):
        writer.append(
            ToolCalled(
                name=tool_name,
                args=args,
                authorised=False,
                reason="field and value must both be strings",
            )
        )
        return {"ok": False, "error": "field and value must both be strings"}

    # Before the write, and separately from it, so the two refusals say
    # different things to the model: one is a key that does not exist, the
    # other is a real key it is too early for. The second is the one that
    # matters — a patient who volunteers the next answer has it refused here
    # and recorded when the question is actually put, which costs them saying
    # it twice; the alternative costs the interview its ending.
    at = machine.position(field)
    if at is not None and at > machine.index:
        reason = "field is ahead of the question being asked"
        writer.append(
            ToolCalled(name=tool_name, args=args, authorised=False, reason=reason)
        )
        return {"ok": False, "error": reason}

    thin = _is_thin(machine, field, value)
    if thin is not None:
        writer.append(
            ToolCalled(name=tool_name, args=args, authorised=False, reason=thin)
        )
        return {"ok": False, "error": thin}

    if not machine.capture(field, value):
        writer.append(
            ToolCalled(
                name=tool_name,
                args=args,
                authorised=False,
                reason="field is not in this protocol",
            )
        )
        return {"ok": False, "error": "field is not in this protocol"}

    writer.append(ToolCalled(name=tool_name, args=args, authorised=True))

    # The patient watches the record being written, which is why the notes card
    # is worth a quarter of the screen. Repaint it the moment the field lands.
    if wire is not None:
        await wire.notes(machine.fields())

    result = await _raise_concerns(machine, writer, args, on_concern)
    if result.blocked:
        # The interview does not move on. It stopped here, at this question,
        # and the record should read that way — `machine.complete` must not
        # become true off the back of the answer that ended the call.
        return {"ok": True, "recorded": field, "interview": "ending"}

    _advance(machine, writer)
    return {"ok": True, "recorded": field}


#: Answers that say the patient has something to tell you without telling you
#: it. Matched **whole**, after normalisation, and deliberately never as a
#: prefix: "yes, there's redness around the wound" is an answer and has to land.
#: Everything here is a word a patient uses to hold the floor, not to fill it.
AFFIRMATIONS = frozenset(
    {
        "yes", "yeah", "yeh", "yep", "yup", "yes there is", "yes i do",
        "yes please", "yes i have", "there is", "i do", "i have",
        "sure", "ok", "okay", "right", "mhm", "mm", "mmhm", "uh huh", "aye",
        "correct", "that's right", "thats right", "true", "definitely",
        "absolutely", "of course",
    }
)

_PUNCTUATION = str.maketrans("", "", ".,!?;:'\"")


def _normalise(value: str) -> str:
    return " ".join(value.lower().translate(_PUNCTUATION).split())


def _is_thin(machine: InterviewMachine, field: str, value: str) -> str | None:
    """The refusal reason for an answer that is only an acknowledgement, or None.

    Some questions read as yes/no and ask for content — the closing one above
    all, which is grammatically answerable with *yes* and is in fact the
    patient's one open turn. A model reading it literally records exactly that.
    On `iv_53ff71f5e583` it recorded `anything_else = "Yes"`, which completed
    the interview and hung up on a patient who had just said she had something
    to raise. The record read "Yes" and the thing she wanted to say is not in
    it, or anywhere.

    Which question those are is the author's to declare, not ours to infer:
    `Question.expects_content`. Two bounds keep this from becoming a model of
    its own — it fires only on an exact whole-string match, so any answer with a
    fact in it passes untouched, and only once per question (`machine.press`),
    so a patient who says "yeah" twice is recorded as having said it rather than
    asked a third time.

    The reason string is written to be read by the model, because in the
    single-pass pipeline it is: a refused call re-runs inference once, and this
    sentence is what the next turn is answering.
    """
    state = machine.current
    if state is None or state.question.field_key != field:
        return None
    if not state.question.expects_content:
        return None
    if _normalise(value) not in AFFIRMATIONS:
        return None
    if not machine.press(field):
        return None
    return (
        "the patient has said there is something, but not what it is — "
        "ask them, and record what they say"
    )


async def _raise_concerns(
    machine: InterviewMachine,
    writer: SessionWriter,
    args: dict[str, Any],
    on_concern: Any,
) -> concerns.ConcernResult:
    """Resolve the current question's flags against this answer, and file it.

    Only ever the *current* question's. `field` may name a question that has not
    been asked (the matrix constrains the state, not the key), and a flag
    authored on a question nobody asked is not something this answer raised.
    """
    state = machine.current
    if state is None:
        return concerns.ConcernResult()

    answer = args.get("answer")
    if not isinstance(answer, str):
        answer = None

    named = args.get("flag")
    if not isinstance(named, str) or named == "none":
        named = None

    # A flag the model named that this question does not declare. The tool's
    # enum is the whole protocol's — it is compiled once, before the call — so
    # this is the check that makes it mean the question it was called from.
    ignored = None
    if named is not None and named not in concerns.declared(state.question):
        ignored, named = named, None

    result = concerns.resolve(state.question, answer=answer, named=named)

    writer.append(
        ConcernRaised(
            field=state.question.field_key,
            hits=result.ids(),
            action=result.action,
            answer=answer,
            matched=result.ids("value"),
            judged=result.ids("judged"),
            ignored=ignored,
        )
    )

    # Each hit's own action, never `result.action` — that is the worst action of
    # the whole answer, so an answer raising a soft review and an urgent would
    # look urgent, and one raising an urgent and an `end_call` would not. The
    # second is the case that matters: a call the flag stopped speaks its own
    # authored sentence and must not also promise a call back it has just made.
    if any(hit.flag.action == "urgent_escalate" for hit in result.hits):
        machine.note_urgent()

    # On every authorised capture, whatever it raised: `EndOfInterview` is what
    # stops the call, and it is reached from here.
    if on_concern is not None:
        await on_concern(result)

    return result


def _advance(machine: InterviewMachine, writer: SessionWriter) -> None:
    """A question is done once its field is captured.

    Nothing else advances the interview — no counter, no turn budget.
    """
    state = machine.current
    if state is None:
        return
    if not machine.captured.get(state.question.field_key):
        return

    nxt = machine.advance()
    writer.append(
        StateTransition(**{"from": state.id, "to": nxt.id if nxt else "complete"})
    )


def _coerce(arguments: Any) -> dict[str, Any] | None:
    """Models emit arguments as a JSON *string*; Pipecat sometimes parses first."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
