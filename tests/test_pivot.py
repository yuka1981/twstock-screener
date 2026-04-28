import numpy as np

from twstock_screener.pivot import find_pivots


def test_find_pivots_simple_sine():
    x = np.linspace(0, 4 * np.pi, 100)
    close = np.sin(x) + 5
    peaks, valleys = find_pivots(close, distance=5, prominence_factor=0.3)
    assert len(peaks) == 2
    assert len(valleys) == 2


def test_find_pivots_alternating():
    """Peaks and valleys should alternate roughly."""
    x = np.linspace(0, 6 * np.pi, 200)
    close = np.sin(x) * 10 + 50
    peaks, valleys = find_pivots(close)
    merged = sorted([(p, "peak") for p in peaks] + [(v, "valley") for v in valleys])
    for a, b in zip(merged, merged[1:], strict=False):
        assert a[1] != b[1]


def test_find_pivots_flat_returns_empty():
    close = np.full(100, 50.0)
    peaks, valleys = find_pivots(close)
    assert peaks == [] and valleys == []


def test_find_pivots_distance_constraint():
    """Adjacent peaks must respect distance parameter."""
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.standard_normal(200)) + 100
    peaks, _ = find_pivots(close, distance=10)
    for a, b in zip(peaks, peaks[1:], strict=False):
        assert b - a >= 10
