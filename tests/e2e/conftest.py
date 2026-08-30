"""The three processes a browser test needs, and the browser that drives them.

`tests/e2e/patient.py` already proves the *media* path — it joins a room over
LiveKit and listens. What it cannot do is be a browser: it has no page to
reload, no tab to close, and no `pagehide`. So every bug that lives in the gap
between "the user left" and "the server noticed" was invisible to the suite,
and both of the ones found on 2026-08-30 lived exactly there.

This file closes that gap by running the real thing — `livekit-server`, the
backend, and Vite — and pointing headless Chromium at it.

Ports are deliberately *not* the dev ports. A test that attached to a running
`make dev` would be testing whatever code that process started with, which is
the one thing a test must never do; and one that killed it would be a test that
eats your afternoon. `API_TARGET` in `frontend/call/vite.config.ts` exists for
this and nothing else.

The SFU is the exception: it holds none of our code, so a `livekit-server`
already listening on :7880 is reused rather than duplicated.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
import wave
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Not 3000/5173/7880. See the module docstring.
API_PORT = 3100
WEB_PORT = 5273
SFU_PORT = 7880

API = f"http://127.0.0.1:{API_PORT}"
WEB = f"http://127.0.0.1:{WEB_PORT}"

#: Vite is the slow one — a cold start compiles the workspace.
BOOT_TIMEOUT_S = 90.0


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, what: str, proc: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if _listening(port):
            return
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"{what} exited with {proc.returncode} before it listened on {port}")
        time.sleep(0.2)
    raise RuntimeError(f"{what} never listened on :{port} within {BOOT_TIMEOUT_S:.0f}s")


def _spawn(argv: list[str], env: dict[str, str], log: Path) -> subprocess.Popen:
    """Start a process with its output on disk.

    On disk rather than through a pipe: a pipe nobody drains fills its buffer
    and wedges the child, and Vite is chatty enough to do it. The file is also
    what a failing run needs to read.
    """
    handle = log.open("w", encoding="utf-8")
    return subprocess.Popen(
        argv,
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        # The backend drains live calls on SIGTERM — that is the deploy path,
        # and waiting for it here is the same courtesy a deploy extends.
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def e2e_logs() -> Path:
    out = REPO_ROOT / "logs" / "e2e"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture(scope="session")
def groq_key() -> str:
    """A real key, and the reason is the honesty of the assertions.

    The assistant speaks the moment the patient joins, so with no key the
    pipeline errors and the session ends *on its own* — and every "the slot was
    freed" assertion below would pass without the code under test doing
    anything at all. A test that passes for the wrong reason is worse than no
    test, so this skips instead.

    Nothing is ever spoken *to* the assistant (see `silence`), so the cost of a
    run is one short greeting per test.
    """
    # Through `config`, not `os.environ`: it is what loads `.env`, and it is
    # where the backend about to be started will read the same value from.
    from services.core.config import GROQ_API_KEY

    key = GROQ_API_KEY.strip()
    if not key:
        pytest.skip("GROQ_API_KEY is empty — see the docstring for why this cannot be faked")
    return key


@pytest.fixture(scope="session")
def sfu(e2e_logs: Path):
    """`livekit-server --dev`, or the one already running.

    Reusable because it holds none of our code: rooms are named per session
    (`intake-s_…`), so a dev SFU shared with a `make dev` cannot collide.
    """
    if _listening(SFU_PORT):
        yield
        return
    if shutil.which("livekit-server") is None:
        pytest.skip("livekit-server is not installed — see `make doctor`")

    proc = _spawn(["livekit-server", "--dev"], {}, e2e_logs / "sfu.log")
    try:
        _wait_for_port(SFU_PORT, "livekit-server", proc)
        yield
    finally:
        _terminate(proc)


@pytest.fixture(scope="session")
def backend(sfu, groq_key: str, e2e_logs: Path):
    """The FastAPI + Pipecat process, on its own port and its own record.

    `DATABASE_URL` and `SUPABASE_URL` are emptied rather than inherited:
    `config.py` reads `.env` with `setdefault`, so the repo's own populated
    `DATABASE_URL` would otherwise have a browser test writing rows into the
    hosted project — through triggers that then refuse to let anyone delete
    them. Empty means the JSONL writer and the in-process store, which is
    exactly what a test wants.
    """
    env = {
        "PORT": str(API_PORT),
        "GROQ_API_KEY": groq_key,
        "DATABASE_URL": "",
        "SUPABASE_URL": "",
        "PORTAL_URL": WEB,
        # Two of `POST /session`'s four refusals would otherwise fire on the
        # suite itself rather than on anything under test — every call here
        # comes from one address in a couple of minutes, which is the exact
        # shape the per-IP limiter exists to turn away, and the daily quota is
        # shared with whatever else has run today. Both are held by
        # `tests/test_limits.py`, where they can be tested for what they are.
        "MAX_SESSIONS_PER_DAY": "10000",
        "RATE_LIMIT_BURST": "10000",
    }
    proc = _spawn(["uv", "run", "python", "-m", "services.core.app"], env, e2e_logs / "api.log")
    try:
        _wait_for_port(API_PORT, "backend", proc)
        yield
    finally:
        _terminate(proc)


@pytest.fixture(scope="session")
def portal(backend, e2e_logs: Path):
    """Vite, serving the patient portal and proxying `/api` to *our* backend."""
    # The workspace's own `dev` script (`vite`), not the root's `dev:call`
    # wrapper: an argument passed through two layers of `npm run` is eaten by
    # the second one, and vite silently kept its default port while we waited
    # for ours. `--strictPort` so a clash is an error rather than a drift.
    env = {"API_TARGET": API}
    proc = _spawn(
        [
            "npm",
            "run",
            "--silent",
            "dev",
            "--workspace=@metafora/call",
            "--",
            "--port",
            str(WEB_PORT),
            "--strictPort",
            # Explicit, because vite's default `localhost` resolves to ::1 on
            # macOS — it comes up, and nothing reaching it over 127.0.0.1 finds
            # it, browser included.
            "--host",
            "127.0.0.1",
        ],
        env,
        e2e_logs / "web.log",
    )
    try:
        _wait_for_port(WEB_PORT, "vite", proc)
        yield WEB
    finally:
        _terminate(proc)


@pytest.fixture(scope="session")
def silence(tmp_path_factory) -> Path:
    """A second of digital silence for Chromium's fake microphone.

    Chrome's built-in fake device emits a tone, which the VAD would hear as a
    patient talking — every run would then transcribe, generate and speak,
    which is a conversation test wearing a lifecycle test's clothes. Silence
    keeps a run deterministic and its cost to the one greeting.
    """
    path = tmp_path_factory.mktemp("audio") / "silence.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 16_000)
    return path


@pytest.fixture
async def page(portal: str, silence: Path):
    """A headless Chromium with a microphone, one per test.

    Playwright's *async* API and a browser per test, both for the same reason:
    this repo runs pytest in `asyncio_mode = "auto"`, so there is always a loop
    running — the sync API refuses to start inside one, and a session-scoped
    browser would outlive the loop it was launched on. Chromium starts in about
    a second, which is nothing next to the call each test then makes.

    The media flags are the patient's tap: `getUserMedia` is the first thing
    `useCall.start` does and the entire call is behind it, so a browser that
    would have shown a permission prompt is a browser that tests nothing.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={silence}",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = await browser.new_context(permissions=["microphone"])
        try:
            yield await context.new_page()
        finally:
            await context.close()
            await browser.close()


@pytest.fixture
def live():
    """How many calls the backend thinks are in progress.

    `GET /health` already reports this for the Fly check (`app.py`), and it is
    the whole oracle for these tests: a slot still held after the patient has
    gone is the bug, stated as a number.
    """

    async def read() -> int:
        async with httpx.AsyncClient(timeout=5.0) as http:
            return (await http.get(f"{API}/health")).json()["live"]

    return read


async def capacity() -> int:
    """How many concurrent calls this box admits — `MAX_CONCURRENT_SESSIONS`,
    read from the running backend rather than imported, because the number that
    matters is the one it booted with."""
    async with httpx.AsyncClient(timeout=5.0) as http:
        return (await http.get(f"{API}/health")).json()["capacity"]


@pytest.fixture
def record():
    """A session's own JSONL — the record itself, with no database configured.

    Worth reaching for from a browser test in exactly one case: when what the
    patient saw is right and what was *written down* is wrong. A call that ends
    for one reason and is filed under another looks identical from the page.
    """

    def read(session_id: str) -> list[dict]:
        path = REPO_ROOT / "logs" / f"{session_id}.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # the writer is mid-flush; the next poll gets it
        return events

    return read


@pytest.fixture
def until():
    """Poll until a condition holds, then return how long it took.

    Freeing a slot is not instant on any path — a `keepalive` POST is in
    flight, or the browser is still tearing the page down — so the assertion
    worth making is "within", not "immediately". `asyncio.sleep`, never
    `time.sleep`: this shares its loop with the browser it is waiting on.
    """

    async def wait(predicate, timeout: float, what: str) -> float:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if await predicate():
                return time.monotonic() - started
            await asyncio.sleep(0.1)
        raise AssertionError(f"{what} — still false after {timeout:.0f}s")

    return wait
