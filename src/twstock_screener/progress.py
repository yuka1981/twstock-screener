"""Lightweight progress reporter — stdlib only, tty-aware.

Single-threaded only; do not share a ProgressReporter across threads.
"""
from __future__ import annotations

import sys
import time
from datetime import timedelta
from typing import TextIO


class ProgressReporter:
    def __init__(
        self,
        total: int,
        label: str = "",
        stream: TextIO | None = None,
        log_every: int = 50,
    ) -> None:
        self.total = total
        self.label = label
        self.stream = stream if stream is not None else sys.stderr
        self.log_every = max(1, log_every)
        self.start = time.monotonic()
        self.n = 0
        self.is_tty = self._detect_tty(self.stream)

    @staticmethod
    def _detect_tty(stream: TextIO) -> bool:
        isatty = getattr(stream, "isatty", None)
        return bool(isatty()) if callable(isatty) else False

    def _format(self, suffix: str) -> str:
        elapsed = max(time.monotonic() - self.start, 1e-9)
        rate = self.n / elapsed if self.n else 0.0
        pct = (self.n / self.total * 100) if self.total else 0.0
        if rate > 0 and self.total:
            eta = str(timedelta(seconds=int((self.total - self.n) / rate)))
        else:
            eta = "-"
        msg = (
            f"{self.label} [{self.n}/{self.total}] "
            f"{pct:.1f}% rate={rate:.2f}/s eta={eta}"
        )
        return f"{msg}  {suffix}" if suffix else msg

    def update(self, n: int = 1, suffix: str = "") -> None:
        self.n += n
        msg = self._format(suffix)
        if self.is_tty:
            self.stream.write(f"\r{msg}\033[K")
            self.stream.flush()
            if self.n % self.log_every == 0 or self.n >= self.total:
                self.stream.write("\n")
                self.stream.flush()
        elif self.n % self.log_every == 0 or self.n >= self.total:
            self.stream.write(msg + "\n")
            self.stream.flush()

    def close(self) -> None:
        if self.is_tty and self.n > 0:
            self.stream.write("\n")
            self.stream.flush()
