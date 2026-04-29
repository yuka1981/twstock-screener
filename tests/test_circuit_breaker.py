# tests/test_circuit_breaker.py
from datetime import datetime, timedelta

from twstock_screener.circuit_breaker import CircuitBreaker


def test_starts_closed():
    cb = CircuitBreaker(threshold=50, cooldown_seconds=1800)
    assert not cb.is_open()


def test_opens_after_threshold():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1800)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open()


def test_success_resets_counter():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1800)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open()  # only 2 consecutive


def test_cooldown_closes_after_window():
    now = datetime(2026, 4, 28, 3, 0, 0)
    cb = CircuitBreaker(threshold=2, cooldown_seconds=60, _now=lambda: now)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open()
    cb._now = lambda: now + timedelta(seconds=61)
    assert not cb.is_open()
