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


# --- Split-discontinuity guard (cycle #29 (c) hardening) ----------------
#
# TWSE daily price limit is ±10% → max legitimate adjacent-bar ratio is
# ~1.111. Ratios > 1.5 indicate corporate action (split / capital
# reduction / large distribution) that destroys signal continuity. Empty
# pivots returned for graceful degradation per §4.1-derived threshold.
#
# Threshold derivation (§4.1 invocation log):
#   p99.5 normal trading max_adj = 1.122 (empirical, 1255 stocks)
#   Smallest clear corporate-action max_adj = 5.0 (00674R)
#   Threshold = 1.5 (38pp margin above normal envelope, below clear
#   corporate-action floor)


def test_find_pivots_logs_warning_on_split_discontinuity(caplog):
    """Series with > 1.5× adjacent-bar ratio (mimics 00631L pre-purge
    state) must emit WARNING log identifying split-discontinuity. Without
    the warning, downstream callers see empty pivots but have no
    mechanistic explanation — exactly the silent-failure mode (c)
    hardening exists to eliminate."""
    import logging
    pre = np.linspace(450, 500, 30) + np.sin(np.linspace(0, 4 * np.pi, 30)) * 10
    post = np.linspace(25, 30, 30) + np.sin(np.linspace(0, 4 * np.pi, 30)) * 1
    close = np.concatenate([pre, post])

    with caplog.at_level(logging.WARNING, logger="twstock_screener.pivot"):
        peaks, valleys = find_pivots(close)

    assert peaks == [] and valleys == []
    assert any("split" in rec.message.lower() or "discontinuity" in rec.message.lower()
               or "adjacent-bar ratio" in rec.message.lower()
               for rec in caplog.records), (
        f"expected warning about discontinuity; got: {[r.message for r in caplog.records]}"
    )


def test_find_pivots_no_warning_under_split_threshold(caplog):
    """Adjacent-bar ratio <= 1.5 (e.g., leveraged-ETF distribution day)
    must NOT trigger the discontinuity warning. Otherwise warning becomes
    noise, and the protocol-spirit observation 'distinguish discontinuity
    from legitimate-but-large move' fails."""
    import logging
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.standard_normal(60) * 0.5)
    close[30] = close[29] * 0.85  # one -15% bar simulating distribution

    with caplog.at_level(logging.WARNING, logger="twstock_screener.pivot"):
        find_pivots(close)

    discontinuity_logs = [r for r in caplog.records
                          if "discontinuity" in r.message.lower()
                          or "adjacent-bar" in r.message.lower()]
    assert not discontinuity_logs, (
        "1.176× ratio below 1.5 threshold — guard misfired"
    )


def test_find_pivots_returns_empty_on_split_regardless_of_geometry():
    """Even when post-split geometry would produce pivots in isolation,
    the discontinuity guard must return empty pivots (deterministic
    behavior, not implicit via std-inflation)."""
    pre = np.linspace(450, 500, 30) + np.sin(np.linspace(0, 4 * np.pi, 30)) * 10
    post = np.linspace(25, 30, 30) + np.sin(np.linspace(0, 4 * np.pi, 30)) * 1
    close = np.concatenate([pre, post])
    peaks, valleys = find_pivots(close)
    assert peaks == [] and valleys == []
