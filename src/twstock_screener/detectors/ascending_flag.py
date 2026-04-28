# src/twstock_screener/detectors/ascending_flag.py
import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult


class AscendingFlagDetector:
    pattern_id: str = "ascending_flag"
    confidence_weight: float = 0.80
    lookback_days: int = 25

    POLE_BARS = 8
    FLAG_BARS = 12

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)

        pole = close[: self.POLE_BARS]
        flag_end = self.POLE_BARS + self.FLAG_BARS
        flag = close[self.POLE_BARS : flag_end]
        rest = close[flag_end:]

        if len(pole) < 5 or len(flag) < 5 or len(rest) < 1:
            return self._no(df)

        # Flagpole: linear regression slope strongly positive.
        x = np.arange(len(pole))
        slope_pole = float(np.polyfit(x, pole, 1)[0])
        mean_pole = float(pole.mean())
        if slope_pole <= 0.02 * mean_pole:
            return self._no(df)
        pole_rise = float(pole[-1] - pole[0])

        # Flag: parallel negative-slope upper and lower lines.
        x_flag = np.arange(len(flag))
        flag_high = df["high"].to_numpy(dtype=float)[self.POLE_BARS : flag_end]
        flag_low = df["low"].to_numpy(dtype=float)[self.POLE_BARS : flag_end]
        slope_high = float(np.polyfit(x_flag, flag_high, 1)[0])
        slope_low = float(np.polyfit(x_flag, flag_low, 1)[0])
        if slope_high >= 0 or slope_low >= 0:
            return self._no(df)
        slope_diff_ratio = abs(slope_high - slope_low) / abs((slope_high + slope_low) / 2)
        if slope_diff_ratio > 0.30:
            return self._no(df)

        flag_amplitude = float(flag.max() - flag.min())
        if flag_amplitude > pole_rise * 0.50:
            return self._no(df)

        # Breakout: latest close above upper channel projection.
        last_x = len(flag) - 1 + len(rest)
        intercept_high = float(np.polyfit(x_flag, flag_high, 1)[1])
        upper_at_last = slope_high * last_x + intercept_high
        last_close = float(close[-1])
        if last_close <= upper_at_last:
            return self._no(df)

        break_strength = float(np.clip((last_close - upper_at_last) / upper_at_last / 0.02, 0.0, 1.0))
        parallelism = 1.0 - slope_diff_ratio / 0.30
        fit = float(np.clip(parallelism * break_strength, 0.0, 1.0))
        return DetectorResult(
            matched=True,
            fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={
                "slope_pole": slope_pole,
                "slope_high": slope_high,
                "slope_low": slope_low,
                "pole_rise": pole_rise,
                "upper_at_last": upper_at_last,
            },
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(
            matched=False,
            fit_score=0.0,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
        )
