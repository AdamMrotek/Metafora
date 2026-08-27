"""What stands between an unauthenticated route and an unbounded bill.

`POST /session` is unauthenticated and each hit starts an LLM + TTS session, so
the cheapest possible abuse is a loop. Two limiters here, defending different
things:

`RateLimiter` is per-IP and defends against *one* caller looping. A token bucket
rather than a fixed window: a patient who reloads twice because the first attempt
looked stuck should not be told to come back in five minutes, and a loop still
converges on the refill rate.

`DailyQuota` is global and defends against the thing the per-IP limiter cannot
touch — many callers, or one caller with many addresses. Per-IP plus concurrency
still permits three calls back to back forever, which on a public link is a bill
nobody chose.

Both are in-process. One process holds every call, so a shared store would be a
second dependency bought to defend a box that can only hold three calls anyway.
The cost is that a restart forgets the day's count, which is why the account-level
spend limit at the provider is the real ceiling and this is the polite one.

`now` is injected into both so the tests can move time without sleeping.
"""

import time
from collections.abc import Callable
from datetime import UTC, date, datetime


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


class DailyQuota:
    """Sessions per UTC day, across every caller.

    Deliberately not a rolling window: "200 a day, reset at midnight" is a
    sentence an operator can hold in their head and reason about a bill from.
    A rolling window is more elegant and less explicable.

    UTC rather than local time so the reset does not move twice a year.
    """

    def __init__(
        self,
        limit: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        #: Sessions permitted per day. `0` means no ceiling at all.
        self.limit = max(0, limit)
        self._now = now
        self._day: date | None = None
        self._used = 0

    def _roll(self, today: date) -> None:
        if self._day != today:
            self._day = today
            self._used = 0

    def allow(self) -> bool:
        """Spend one of today's sessions if any remain."""
        if self.limit == 0:
            return True
        self._roll(self._now().date())
        if self._used >= self.limit:
            return False
        self._used += 1
        return True

    def used(self) -> int:
        """How many of today's sessions are spent. For `/health`, not for a
        decision — a caller learns nothing about which limit turned them away."""
        if self.limit == 0:
            return 0
        self._roll(self._now().date())
        return self._used
