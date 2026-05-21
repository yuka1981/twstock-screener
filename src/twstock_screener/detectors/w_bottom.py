# src/twstock_screener/detectors/w_bottom.py
import numpy as np
import pandas as pd

from twstock_screener.detectors.atr import compute_atr
from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class WBottomDetector:
    pattern_id: str = "w_bottom"
    confidence_weight: float = 0.65
    lookback_days: int = 60

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        peaks, valleys = find_pivots(close, distance=5, prominence_factor=0.5)

        if len(valleys) < 2 or len(peaks) < 1:
            return self._no(df)

        v1_idx, v2_idx = valleys[-2], valleys[-1]
        spacing = v2_idx - v1_idx
        if not (10 <= spacing <= 40):
            return self._no(df)

        l1, l2 = float(close[v1_idx]), float(close[v2_idx])
        depth_diff = abs(l1 - l2) / min(l1, l2)
        if depth_diff > 0.03:
            return self._no(df)

        peaks_between = [p for p in peaks if v1_idx < p < v2_idx]
        if not peaks_between:
            return self._no(df)
        p_idx = max(peaks_between, key=lambda p: float(close[p]))
        neckline = float(close[p_idx])
        if neckline < max(l1, l2) * 1.05:
            return self._no(df)

        if v2_idx >= len(close) - 3:
            return self._no(df)
        last_close = float(close[-1])
        if last_close <= neckline:
            return self._no(df)

        atr_20 = compute_atr(df, period=14)
        break_strength = float(np.clip((last_close - neckline) / atr_20, 0.0, 1.0)) if atr_20 > 0 else 0.0
        symmetry = 1.0 - depth_diff / 0.03
        fit = float(np.clip(symmetry * break_strength, 0.0, 1.0))

        return DetectorResult(
            matched=True,
            fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={
                "l1": l1,
                "l2": l2,
                "neckline": neckline,
                "spacing": float(spacing),
                "break_strength": break_strength,
            },
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(
            matched=False,
            fit_score=0.0,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
        )
