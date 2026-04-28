# src/twstock_screener/detectors/m_top.py
import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class MTopDetector:
    pattern_id: str = "m_top"
    confidence_weight: float = 1.00
    lookback_days: int = 60

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None

        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        peaks, valleys = find_pivots(close, distance=5, prominence_factor=0.5)

        if len(peaks) < 2 or len(valleys) < 1:
            return self._no_match(df)

        p1_idx, p2_idx = peaks[-2], peaks[-1]
        spacing = p2_idx - p1_idx
        if not (10 <= spacing <= 40):
            return self._no_match(df)

        h1, h2 = float(close[p1_idx]), float(close[p2_idx])
        height_diff_ratio = abs(h1 - h2) / max(h1, h2)
        if height_diff_ratio > 0.03:
            return self._no_match(df)

        valleys_between: list[int] = [v for v in valleys if p1_idx < v < p2_idx]
        if not valleys_between:
            return self._no_match(df)
        v_idx = max(valleys_between, key=lambda v: -float(close[v]))
        neckline = float(close[v_idx])
        if neckline > min(h1, h2) * 0.95:
            return self._no_match(df)

        if p2_idx >= len(close) - 3:
            return self._no_match(df)

        last_close = float(close[-1])
        if last_close >= neckline:
            return self._no_match(df)

        break_strength = float(np.clip((neckline - last_close) / neckline / 0.02, 0.0, 1.0))
        symmetry = 1.0 - height_diff_ratio / 0.03
        fit = float(np.clip(symmetry * break_strength, 0.0, 1.0))

        return DetectorResult(
            matched=True,
            fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={
                "h1": h1,
                "h2": h2,
                "neckline": neckline,
                "spacing": float(spacing),
                "break_strength": break_strength,
            },
        )

    def _no_match(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(
            matched=False,
            fit_score=0.0,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
        )
