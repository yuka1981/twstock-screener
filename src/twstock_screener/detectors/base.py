# src/twstock_screener/detectors/base.py
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class DetectorResult:
    matched: bool
    fit_score: float
    anchor_date: date
    debug: dict[str, float] = field(default_factory=dict)


class Detector(Protocol):
    pattern_id: str
    confidence_weight: float
    lookback_days: int

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None: ...
