import math

MIN_VOLUME = 1_000_000


def liquidity_factor(avg_volume_20d: float) -> float:
    if avg_volume_20d < MIN_VOLUME:
        return 0.0
    raw = math.log10(avg_volume_20d / MIN_VOLUME)
    return max(0.0, min(2.0, raw)) / 2.0


def composite_score(
    fit_score: float, confidence_weight: float, avg_volume_20d: float
) -> float:
    return fit_score * confidence_weight * liquidity_factor(avg_volume_20d)
