"""Tool dispatch, with the permission matrix in front of it.

The matrix is checked here rather than described to the model, so a call from a
state the protocol does not allow is refused whatever the model decided. That is
the difference between a guarantee and a request: `machine.authorise` runs in
our process, on a call the model cannot route around.

Three refusal modes, each recorded with `authorised=False` so the audit trail
shows the attempt as well as the outcome:

  · arguments that are not the shape the schema declared
  · a call from a state the matrix does not name
  · a field key the protocol never declared

Every one of them still returns a result to the model. A dangling tool call
corrupts the next turn's context, and the model is entitled to see what its call
actually did.
"""

import json
from typing import Any

from services.agent.machine import InterviewMachine
from services.agent.session_log import SessionWriter, StateTransition, ToolCalled


async def dispatch(
    *,
    machine: InterviewMachine,
    writer: SessionWriter,
    wire: Any,
    tool_name: str,
    arguments: Any,
) -> dict[str, Any]:
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

    _advance(machine, writer)
    return {"ok": True, "recorded": field}


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
