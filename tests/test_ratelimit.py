import time

from twstock_screener.ratelimit import TokenBucket


def test_acquire_initial_burst_no_wait() -> None:
    """3 tokens available immediately."""
    bucket = TokenBucket(capacity=3, refill_rate=0.6)
    start = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    assert time.monotonic() - start < 0.5


def test_acquire_blocks_when_empty() -> None:
    bucket = TokenBucket(capacity=3, refill_rate=0.6, jitter_pct=0.0)
    for _ in range(3):
        bucket.acquire()
    start = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - start
    assert 1.4 < elapsed < 2.2  # ~1.67s refill


def test_jitter_within_pct() -> None:
    bucket = TokenBucket(capacity=1, refill_rate=1.0, jitter_pct=0.10)
    for _ in range(3):
        bucket.acquire()
    start = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - start
    # base ~1.0s ± 10% → 0.9~1.1s
    assert 0.85 < elapsed < 1.20
