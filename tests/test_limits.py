"""The per-IP token bucket in front of `POST /session`.

Time is injected, so none of this sleeps.
"""

from services.core.limits import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _limiter(burst=3, window_s=300.0):
    clock = Clock()
    return RateLimiter(burst=burst, window_s=window_s, now=clock), clock


def test_the_burst_is_spendable_immediately():
    """A patient who reloads twice because the first attempt looked stuck is not
    abusing anything, and must not be told to come back in five minutes.
    """
    limiter, _ = _limiter(burst=3)
    assert [limiter.allow("ip") for _ in range(3)] == [True, True, True]
    assert limiter.allow("ip") is False


def test_it_refills_at_the_window_rate():
    limiter, clock = _limiter(burst=3, window_s=300.0)
    for _ in range(3):
        limiter.allow("ip")
    assert limiter.allow("ip") is False

    clock.advance(100.0)  # one third of the window · one token
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False


def test_it_refills_no_further_than_the_burst():
    limiter, clock = _limiter(burst=2, window_s=10.0)
    limiter.allow("ip")
    clock.advance(10_000.0)
    assert [limiter.allow("ip") for _ in range(3)] == [True, True, False]


def test_retry_after_is_when_the_next_token_lands():
    limiter, clock = _limiter(burst=3, window_s=300.0)
    for _ in range(3):
        limiter.allow("ip")

    assert limiter.retry_after("ip") == 100
    clock.advance(60.0)
    assert limiter.retry_after("ip") == 40
    clock.advance(40.0)
    assert limiter.retry_after("ip") == 0


def test_retry_after_is_zero_for_a_caller_with_tokens():
    limiter, _ = _limiter()
    assert limiter.retry_after("never-seen") == 0


def test_callers_do_not_pay_for_each_other():
    limiter, _ = _limiter(burst=1)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True


def test_a_refused_attempt_does_not_extend_the_wait():
    """Hammering the door must not make the wait longer — that turns a retry
    loop into a permanent lockout for anyone behind the same NAT.
    """
    limiter, clock = _limiter(burst=1, window_s=100.0)
    limiter.allow("ip")
    for _ in range(50):
        limiter.allow("ip")

    clock.advance(100.0)
    assert limiter.allow("ip") is True


def test_refilled_callers_are_forgotten():
    """Otherwise a crawler with a fresh IP per request is a slow memory leak."""
    limiter, clock = _limiter(burst=1, window_s=1.0)
    for i in range(2000):
        limiter.allow(f"ip-{i}")

    clock.advance(10.0)
    limiter.allow("one-more")
    assert len(limiter._buckets) < 2000
