"""Per-IP rate limiting for session starts.

`POST /session` is unauthenticated and each hit starts an LLM + TTS session, so
the cheapest possible abuse is a loop. One process holds every call, so the
limiter is in-process too — a shared store would be a second dependency bought
to defend a box that can only hold three calls anyway.

A token bucket rather than a fixed window: a patient who reloads twice because
the first attempt looked stuck should not be told to come back in five minutes,
and a loop still converges on the refill rate.

`now` is injected so the tests can move time without sleeping.
"""

import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        burst: int,
        window_s: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        #: Tokens available immediately, and the ceiling refill counts up to.
        self.burst = max(1, burst)
        #: Seconds to refill an empty bucket to full.
        self.window_s = max(1e-6, window_s)
        self._now = now
        #: key → (tokens remaining, when that was true)
        self._buckets: dict[str, tuple[float, float]] = {}

    @property
    def _rate(self) -> float:
        """Tokens per second."""
        return self.burst / self.window_s

    def _level(self, key: str, now: float) -> float:
        tokens, updated = self._buckets.get(key, (float(self.burst), now))
        return min(float(self.burst), tokens + (now - updated) * self._rate)

    def allow(self, key: str) -> bool:
        """Spend a token if there is one. Records the attempt either way."""
        now = self._now()
        tokens = self._level(key, now)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        self._buckets[key] = (tokens, now)
        self._prune(now)
        return allowed

    def retry_after(self, key: str) -> int:
        """Whole seconds until `allow` would succeed. Never negative."""
        now = self._now()
        missing = 1.0 - self._level(key, now)
        if missing <= 0:
            return 0
        return max(1, round(missing / self._rate))

    def _prune(self, now: float) -> None:
        """Forget anyone whose bucket has refilled — they are indistinguishable
        from a caller we have never seen, and holding them is a slow leak.
        """
        if len(self._buckets) < 1024:
            return
        self._buckets = {
            key: state
            for key, state in self._buckets.items()
            if self._level(key, now) < self.burst
        }
