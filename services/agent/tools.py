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

    `on_concern` is how the verdict gets out of here, and it is called on every
    authorised capture rather than only the ones that raise something. This runs
    inside the capture branch of the `ParallelPipeline` and cannot push a frame
    of its own — the speech pass is answering the same turn beside it. See
    `EndOfInterview.answered`.
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

    # On every authorised capture, whatever it raised. `EndOfInterview` holds
    # the assistant's reply on a question that can stop the call, and this is
    # what tells it the answer is in — a hold released only by a concern would
    # never be released by a clean answer.
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
