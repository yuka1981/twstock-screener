import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class AscendingWedgeDetector:
    pattern_id: str = "ascending_wedge"
    confidence_weight: float = 1.00
    lookback_days: int = 40

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        volume = df["volume"].to_numpy(dtype=float)

        peaks, valleys = find_pivots(close, distance=3, prominence_factor=0.3)
        if len(peaks) < 2 or len(valleys) < 2:
            return self._no(df)

        p_idx = np.array(peaks[-2:], dtype=float)
        v_idx = np.array(valleys[-2:], dtype=float)
        slope_high = (high[int(p_idx[1])] - high[int(p_idx[0])]) / (p_idx[1] - p_idx[0])
        slope_low = (low[int(v_idx[1])] - low[int(v_idx[0])]) / (v_idx[1] - v_idx[0])
        if slope_high <= 0 or slope_low <= slope_high:
            return self._no(df)

        intercept_high = high[int(p_idx[1])] - slope_high * p_idx[1]
        last_x = float(len(close) - 1)
        upper_at_last = slope_high * last_x + intercept_high
        last_close = float(close[-1])
        if last_close <= upper_at_last:
            return self._no(df)

        avg_vol_20 = float(volume[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        vol_today = float(volume[-1])
        if vol_today < avg_vol_20 * 1.5:
            return self._no(df)

        convergence = (slope_low - slope_high) / max(slope_low, 1e-9)
        break_strength = float(np.clip((last_close - upper_at_last) / upper_at_last / 0.02, 0.0, 1.0))
        vol_factor = float(np.clip(vol_today / (avg_vol_20 * 1.5) - 1.0, 0.0, 1.0))
        fit = float(np.clip(convergence * break_strength * (0.5 + 0.5 * vol_factor), 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"slope_high": float(slope_high), "slope_low": float(slope_low),
                   "convergence": float(convergence), "upper_at_last": upper_at_last,
                   "vol_today": vol_today, "avg_vol_20": avg_vol_20},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
