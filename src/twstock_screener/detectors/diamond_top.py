# src/twstock_screener/detectors/diamond_top.py
import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class DiamondTopDetector:
    pattern_id: str = "diamond_top"
    confidence_weight: float = 0.65
    lookback_days: int = 50

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        peaks, valleys = find_pivots(close, distance=4, prominence_factor=0.4)

        if len(peaks) < 3 or len(valleys) < 2:
            return self._no(df)

        p_idx = peaks[-3:]
        v_idx = valleys[-2:]
        merged = sorted(
            [(i, "p") for i in p_idx] + [(i, "v") for i in v_idx]
        )
        kinds = [k for _, k in merged]
        if kinds != ["p", "v", "p", "v", "p"]:
            return self._no(df)

        p1, v1, p2, v2, p3 = [i for i, _ in merged]
        amp1 = abs(close[p1] - close[v1])
        amp2 = abs(close[p2] - close[v1])
        amp3 = abs(close[p2] - close[v2])
        amp4 = abs(close[p3] - close[v2])

        if not (amp2 > amp1 and amp3 > amp4):
            return self._no(df)

        symmetry = min(amp1 / amp2, amp4 / amp3)
        if symmetry < 0.5:
            return self._no(df)

        x_v = np.array([v1, v2], dtype=float)
        y_v = np.array([close[v1], close[v2]], dtype=float)
        slope = (y_v[1] - y_v[0]) / (x_v[1] - x_v[0])
        intercept = y_v[0] - slope * x_v[0]
        last_x = len(close) - 1
        lower_proj = slope * last_x + intercept
        last_close = float(close[-1])
        if last_close >= lower_proj:
            return self._no(df)

        break_strength = float(np.clip((lower_proj - last_close) / lower_proj / 0.02, 0.0, 1.0))
        fit = float(np.clip(symmetry * break_strength, 0.0, 1.0))
        return DetectorResult(
            matched=True,
            fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={
                "symmetry": symmetry,
                "amp1": amp1,
                "amp2": amp2,
                "amp3": amp3,
                "amp4": amp4,
                "lower_proj": lower_proj,
            },
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(
            matched=False,
            fit_score=0.0,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
        )
