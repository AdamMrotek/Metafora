"""A `Bot` that never touches LiveKit.

`services/core/app.py` was the only module in the project with no test coverage,
and it is where every deploy blocker lives. It looked untestable because
`POST /session` joins a room — but the route touches only four things on the bot
(`transport`, `wire`, `worker`, and the runner that drives it), so substituting
those makes the whole HTTP surface testable with no SFU, no Groq key and no
network. The suite's no-key contract survives.

The fake mirrors the real ordering rather than shortcutting it: the transport
reports itself connected when the *runner starts*, because that is when it
happens in production, and `POST /session` blocks on exactly that edge.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any


class FakeTransport:
    """Registers handlers the way `LiveKitTransport` does, and fires them on cue."""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.disconnected = False

    def event_handler(self, name: str):
        def register(fn):
            self.handlers.setdefault(name, []).append(fn)
            return fn

        return register

    async def fire(self, name: str, *args) -> None:
        for handler in list(self.handlers.get(name, [])):
            await handler(self, *args)

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeWire:
    """Everything the browser would have been told, in order."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    def _record(self, kind: str, value: Any) -> None:
        self.sent.append((kind, value))

    async def phase(self, phase: str) -> None:
        self._record("phase", phase)

    async def notes(self, fields) -> None:
        self._record("notes", fields)

    async def utterance(self, who: str, text: str, id: str) -> None:
        self._record("utterance", (who, text))

    async def ended(self, reason: str, say: str | None = None) -> None:
        self._record("ended", reason)

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.sent]


class FakeWorker:
    def __init__(self, bot: "FakeBot") -> None:
        #: `_run` hands the runner a worker, not a bot, so the worker is how the
        #: runner finds its way back to the transport it should connect.
        self.bot = bot
        self.frames: list[Any] = []
        self.stopped = False
        #: Stands in for the EndFrame reaching the end of the pipeline. The real
        #: `stop_when_done()` only *queues* that frame and returns, so the delay
        #: between "asked to stop" and "actually stopped" is the thing teardown
        #: has to wait out — and therefore the thing the fake has to reproduce.
        self.finished = asyncio.Event()
        #: Set on a worker that should ignore the EndFrame, so the timeout path
        #: can be exercised.
        self.wedged = False

    async def queue_frames(self, frames) -> None:
        self.frames.extend(frames)

    async def stop_when_done(self) -> None:
        self.stopped = True
        if not self.wedged:
            self.finished.set()

    @property
    def spoken(self) -> str:
        """Whatever was queued for the TTS, as text."""
        return " ".join(getattr(f, "text", "") for f in self.frames).strip()


@dataclass
class FakeBot:
    transport: FakeTransport = field(default_factory=FakeTransport)
    wire: FakeWire = field(default_factory=FakeWire)
    worker: FakeWorker = field(init=False)
    machine: Any = None

    def __post_init__(self) -> None:
        self.worker = FakeWorker(self)


class FakeRunner:
    """Stands in for `WorkerRunner`. Connects, then holds the call open until
    the queued EndFrame drains — which is what the real runner does, and what
    `teardown` now waits for.

    Holding rather than returning immediately is the point: a runner that
    finished at once would end every session the moment it began, and the
    teardown paths under test only exist for calls that are still live.
    """

    def __init__(self, **_kwargs) -> None:
        pass

    async def run(self, worker: FakeWorker) -> None:
        await worker.bot.transport.fire("on_connected")
        await worker.finished.wait()


class BotFactory:
    """One fake bot per `build_bot` call, kept so tests can inspect them."""

    def __init__(self) -> None:
        self.bots: list[FakeBot] = []

    def __call__(self, **_kwargs) -> FakeBot:
        bot = FakeBot()
        self.bots.append(bot)
        return bot

    @property
    def latest(self) -> FakeBot:
        return self.bots[-1]


def install(monkeypatch, module) -> BotFactory:
    """Point `module.build_bot` and `module.WorkerRunner` at the fakes."""
    factory = BotFactory()
    monkeypatch.setattr(module, "build_bot", factory)
    monkeypatch.setattr(module, "WorkerRunner", FakeRunner)
    return factory
