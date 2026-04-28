import numpy as np
from scipy.signal import find_peaks


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
        (peak_indices, valley_indices) — empty lists if input is too short or flat.
    """
    if len(close) < distance * 2 or float(close.std()) == 0.0:
        return [], []
    prominence = float(close.std()) * prominence_factor
    peaks, _ = find_peaks(close, distance=distance, prominence=prominence)
    valleys, _ = find_peaks(-close, distance=distance, prominence=prominence)
    return peaks.tolist(), valleys.tolist()
