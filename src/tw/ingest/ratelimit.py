"""Per-provider token bucket. Every outbound request must go through `acquire()`."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    rate_per_s: float
    capacity: float
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.rate_per_s <= 0 or self.capacity < 1:
            raise ValueError("rate_per_s must be > 0 and capacity >= 1")
        self._tokens = self.capacity
        self._last = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_s)
        self._last = now

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False

    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate_per_s
            self.sleep(wait)


class RateLimiter:
    """Registry of buckets keyed by provider name."""

    def __init__(self, limits: dict[str, tuple[float, float]]) -> None:
        self._buckets = {name: TokenBucket(rate, cap) for name, (rate, cap) in limits.items()}

    def acquire(self, provider: str) -> None:
        try:
            bucket = self._buckets[provider]
        except KeyError:
            raise KeyError(f"no rate limit configured for provider {provider!r}") from None
        bucket.acquire()
