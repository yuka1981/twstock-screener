import numpy as np
import pandas as pd

from twstock_screener.backtest import evaluate_signal


def test_evaluate_signal_sell_correct_when_price_falls():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30),
        "close": [100] * 10 + list(np.linspace(100, 90, 20)),
    })
    r = evaluate_signal(df, signal_idx=10, direction="sell", forward_days=20)
    assert r["correct"] is True
    assert r["forward_return"] < -0.05


def test_evaluate_signal_buy_correct_when_price_rises():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30),
        "close": [100] * 10 + list(np.linspace(100, 110, 20)),
    })
    r = evaluate_signal(df, signal_idx=10, direction="buy", forward_days=20)
    assert r["correct"] is True
    assert r["forward_return"] > 0.05


def test_evaluate_signal_no_data_at_horizon():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=15),
        "close": list(np.linspace(100, 105, 15)),
    })
    r = evaluate_signal(df, signal_idx=10, direction="buy", forward_days=20)
    assert r["correct"] is None
