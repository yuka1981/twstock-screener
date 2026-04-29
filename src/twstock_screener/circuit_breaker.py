# src/twstock_screener/circuit_breaker.py
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class CircuitBreaker:
    threshold: int = 50
    cooldown_seconds: int = 1800
    consecutive_failures: int = 0
    opened_at: datetime | None = None
    _now: Callable[[], datetime] = field(default=datetime.now)

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and self.opened_at is None:
            self.opened_at = self._now()

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self._now() - self.opened_at >= timedelta(seconds=self.cooldown_seconds):
            self.opened_at = None
            self.consecutive_failures = 0
            return False
        return True
