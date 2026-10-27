import pytest

from tw.ingest.ratelimit import RateLimiter, TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_bucket_refills_at_rate():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=1.0, capacity=1, clock=clock, sleep=clock.sleep)
    assert bucket.try_acquire()
    assert not bucket.try_acquire()
    clock.t += 0.5
    assert not bucket.try_acquire()
    clock.t += 0.5
    assert bucket.try_acquire()


def test_acquire_waits_instead_of_exceeding_rate():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=2.0, capacity=1, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        bucket.acquire()
    assert clock.t == pytest.approx(2.0)  # 5 requests at 2/s with burst 1


def test_unknown_provider_is_an_error():
    with pytest.raises(KeyError):
        RateLimiter({"adsb_lol": (1.0, 1)}).acquire("nope")


def test_invalid_bucket():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_s=0, capacity=1)
