import logging

import numpy as np
from scipy.signal import find_peaks

logger = logging.getLogger(__name__)

# Split-discontinuity threshold (cycle #29 (c) hardening).
#
# TWSE daily price limit is +/-10% -> max legitimate adjacent-bar ratio
# is ~1.111. Ratios > 1.5 indicate a corporate action (split / capital
# reduction / large distribution) that destroys signal continuity. Empty
# pivots returned for graceful degradation so downstream callers see
# "insufficient signal" rather than the silent-failure mode that
# surfaced in production 2026-05-22 on 00631L.
#
# Threshold derivation followed §4.1 bound-setting checklist from
# retrospective `2026-05-22-pre-mortem-discipline-and-procedural-
# checklists.md`. Full invocation log in the commit message of the
# change that introduced this constant.
MAX_ADJACENT_RATIO_THRESHOLD: float = 1.5


def _max_adjacent_ratio(close: np.ndarray) -> float:
    """Compute the maximum adjacent-bar ratio max(a/b, b/a) over
    consecutive closes. Returns 1.0 for series shorter than 2."""
    if len(close) < 2:
        return 1.0
    arr = np.asarray(close, dtype=float)
    safe_prev = np.where(arr[:-1] == 0, np.nan, arr[:-1])
    safe_curr = np.where(arr[1:] == 0, np.nan, arr[1:])
    ratios = np.maximum(arr[1:] / safe_prev, arr[:-1] / safe_curr)
    if np.all(np.isnan(ratios)):
        return 1.0
    return float(np.nanmax(ratios))


def find_pivots(
    close: np.ndarray,
    distance: int = 5,
    prominence_factor: float = 0.5,
) -> tuple[list[int], list[int]]:
    """Locate local peak and valley indices in a closing-price series.

    Args:
        close: 1-D array of closing prices.
        distance: minimum bars between pivots.
        prominence_factor: prominence threshold = std(close) * factor.

    Returns:
        (peak_indices, valley_indices) — empty lists if input is too
        short, flat, or contains a split-discontinuity (max adjacent-bar
        ratio > MAX_ADJACENT_RATIO_THRESHOLD).
    """
    if len(close) < distance * 2 or float(close.std()) == 0.0:
        return [], []

    # Non-positive close = a halted/suspended-day all-zero placeholder bar
    # (prod incident 2026-06-17, stock 1314 2026-04-08). This is the most
    # extreme discontinuity possible, yet _max_adjacent_ratio's zero-safe
    # nan-masking silently drops it (np.maximum(real, nan) -> nan, swallowed
    # by nanmax), so the split-ratio guard below never fires. Reject it here
    # before the zero close can become a valley feeding a zero denominator
    # into downstream detector math.
    arr = np.asarray(close, dtype=float)
    if np.any(arr <= 0):
        logger.warning(
            "pivot detection: non-positive close encountered (min %.4f) — "
            "likely a halted/suspended-day placeholder bar. Returning empty "
            "pivots for graceful degradation.",
            float(arr.min()),
        )
        return [], []

    max_adj = _max_adjacent_ratio(arr)
    if max_adj > MAX_ADJACENT_RATIO_THRESHOLD:
        logger.warning(
            "pivot detection: max adjacent-bar ratio %.3f exceeds %.2f "
            "threshold — likely split/corporate-action discontinuity. "
            "Returning empty pivots for graceful degradation.",
            max_adj, MAX_ADJACENT_RATIO_THRESHOLD,
        )
        return [], []

    prominence = float(close.std()) * prominence_factor
    peaks, _ = find_peaks(close, distance=distance, prominence=prominence)
    valleys, _ = find_peaks(-close, distance=distance, prominence=prominence)
    return peaks.tolist(), valleys.tolist()
