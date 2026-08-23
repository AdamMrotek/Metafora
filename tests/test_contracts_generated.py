"""The committed TypeScript must match what the generator produces.

pydantic is the source of truth; the `.ts` under `shared/contracts/src` is
derived and committed so `frontend/call` can build without a Python toolchain.
Committing generated output only works if something notices when it drifts.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_contracts_are_not_stale():
    result = subprocess.run(
        [sys.executable, "scripts/gen_contracts.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{result.stdout}{result.stderr}\n"
        "The committed TypeScript no longer matches the pydantic models. "
        "Run: uv run python scripts/gen_contracts.py"
    )


def test_the_wire_shapes_the_browser_relies_on_still_exist():
    """`frontend/call/src/call/useCall.ts` imports these by name and is not
    being changed by the migration."""
    wire = (ROOT / "shared/contracts/src/wire.ts").read_text()
    types = (ROOT / "shared/contracts/src/types.ts").read_text()

    for symbol in ("ServerMessage", "ClientMessage", "SessionBootstrap", "decodeMessage"):
        assert symbol in wire, f"{symbol} vanished from the generated wire contract"
    for symbol in ("CallPhase", "FieldState", "FieldStatus"):
        assert symbol in types, f"{symbol} vanished from the generated types"


def test_the_four_server_messages_are_all_present():
    """The browser's reducer handles exactly these and ignores anything else,
    so dropping one silently stops part of the screen updating."""
    wire = (ROOT / "shared/contracts/src/wire.ts").read_text()
    for shape in ("UtteranceMessage", "PhaseMessage", "NotesMessage", "EndedMessage"):
        assert shape in wire
    assert "'utterance'" in wire and "'phase'" in wire
    assert "'notes'" in wire and "'ended'" in wire
