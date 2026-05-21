"""Shared Average True Range helper for detector score normalization.

Used to express breakout magnitudes in units of recent volatility, so a
0.5% break on a 0.3% ATR stock scores the same as a 2% break on a 1.2% ATR
stock. Replaces fixed-percent normalization (e.g. `/ 0.02`) that compressed
fit_score on low-volatility names and inflated it on high-volatility ones.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Return ATR over the most recent `period` bars (Wilder simple-mean variant).

    df must contain high, low, close columns indexed by trading day.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close_prev = df["close"].shift(1).fillna(df["close"]).to_numpy(dtype=float)
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - close_prev),
        np.abs(low - close_prev),
    ])
    if len(tr) >= period:
        return float(tr[-period:].mean())
    return float(tr.mean())
