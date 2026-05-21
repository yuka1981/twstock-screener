import numpy as np
import pandas as pd

from twstock_screener.detectors.atr import compute_atr
from twstock_screener.detectors.base import DetectorResult


class RectangleDetector:
    pattern_id: str = "rectangle"
    confidence_weight: float = 0.50
    lookback_days: int = 20

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)

        upper = float(high.max())
        lower = float(low.min())
        mean_close = float(close.mean())
        amplitude = (upper - lower) / mean_close
        if amplitude > 0.08:
            return self._no(df)

        upper_touches = int((high >= upper * 0.99).sum())
        lower_touches = int((low <= lower * 1.01).sum())
        if upper_touches < 3 or lower_touches < 3:
            return self._no(df)

        atr = compute_atr(df, period=14)
        if atr / mean_close > 0.015:
            return self._no(df)

        amp_score = float(np.clip(1.0 - amplitude / 0.08, 0.0, 1.0))
        atr_score = float(np.clip(1.0 - (atr / mean_close) / 0.015, 0.0, 1.0))
        fit = float(np.clip((amp_score + atr_score) / 2, 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"upper": upper, "lower": lower, "amplitude": amplitude,
                   "atr": atr, "upper_touches": float(upper_touches),
                   "lower_touches": float(lower_touches)},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
