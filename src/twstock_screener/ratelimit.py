import random
import threading
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Thread-safe token bucket rate limiter.

    capacity: max tokens (burst size)
    refill_rate: tokens per second
    jitter_pct: ± random jitter on sleep duration

    Default for TWSE: 3 tokens / 5s = capacity 3, refill 0.6/s.
    """

    capacity: int
    refill_rate: float
    jitter_pct: float = 0.10
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * self.refill_rate
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                base_wait = deficit / self.refill_rate
            jitter = base_wait * random.uniform(-self.jitter_pct, self.jitter_pct)
            time.sleep(max(0.0, base_wait + jitter))


# Module-level singleton for TWSE
twse_bucket = TokenBucket(capacity=3, refill_rate=0.6, jitter_pct=0.10)
