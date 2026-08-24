"""Shared fixtures.

Two jobs. Keep session logs out of the repo's own `logs/` directory — a test
that creates a session would otherwise leave a real JSONL file behind — and
reset the module-level state that `services/core` deliberately holds in-process,
so tests cannot leak sessions, tasks or spent rate-limit tokens into each other.
"""

import asyncio
from pathlib import Path

import pytest

from services.agent.session_log import JsonlSessionWriter


@pytest.fixture(autouse=True)
def logs_in_tmp(monkeypatch, tmp_path: Path):
    """Every session written by a test lands under `tmp_path`, not `logs/`."""
    from services.core import store

    monkeypatch.setattr(
        store,
        "JsonlSessionWriter",
        lambda session_id: JsonlSessionWriter(session_id, log_dir=tmp_path / "logs"),
    )
    return tmp_path / "logs"


@pytest.fixture(autouse=True)
async def fresh_state():
    """The backend is stateful by design (it holds live WebRTC connections), so
    the state is module-level and has to be swept between tests rather than
    constructed per test.
    """
    from services.core import app as app_module
    from services.core import store

    yield

    for task in [*app_module._tasks.values(), *app_module._watchdogs.values()]:
        task.cancel()
    # Let the cancellations actually land before the next test starts a call.
    await asyncio.gather(
        *[*app_module._tasks.values(), *app_module._watchdogs.values()],
        return_exceptions=True,
    )
    app_module._tasks.clear()
    app_module._watchdogs.clear()
    app_module._starts._buckets.clear()
    store._sessions.clear()
