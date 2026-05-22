"""Per-pattern LF sweet-spot ranking (spec §2.4).

Implements the §8.4-derived sweet-spot table: for each directional
pattern, the highest-precision LF bucket among gate-applicable cells
(n_decided ≥ 20 per amendment 2026-05-22-B small-n threshold).

Sweet spots were derived from `data/backtest_fixtures/snapshot_regime_3a.csv`
(commit ddfd4a8). Re-derive when material drift is detected per spec
§8.6 monitoring — re-run P4 backtest, re-apply §8.4 gate, update this
table. Do NOT hand-edit values without going through the §8.3 step 4
calibration procedure.

Patterns absent from SWEET_SPOTS (rectangle, or any future pattern
whose buckets are all gate-deferred) fall back to composite-only sort
per amendment 2026-05-22-B §2.4 propagation rule.
"""
from __future__ import annotations

from twstock_screener.backtest import LF_BUCKETS
from twstock_screener.score import liquidity_factor

# pattern_id → (bucket_label, thin_rank_flag)
# thin_rank_flag = True when the pattern has only one gate-applicable
# bucket, so the in-bucket boost provides no within-bucket
# differentiation. Informational; doesn't change sort behavior.
SWEET_SPOTS: dict[str, tuple[str, bool]] = {
    "m_top": ("[0.0, 0.3)", False),
    "descending_flag": ("[0.0, 0.3)", True),
    "diamond_top": ("[0.6, 0.9)", False),
    "w_bottom": ("[0.3, 0.6)", False),
    "ascending_flag": ("[0.0, 0.3)", True),
    "ascending_wedge": ("[0.3, 0.6)", False),
}

_BUCKET_BOUNDS: dict[str, tuple[float, float]] = {
    label: (lo, hi) for label, lo, hi in LF_BUCKETS
}


def is_in_sweet_spot(pattern: str, lf: float) -> bool:
    spot = SWEET_SPOTS.get(pattern)
    if spot is None:
        return False
    label, _thin = spot
    lo, hi = _BUCKET_BOUNDS[label]
    return lo <= lf < hi


def _candidate_lf(c) -> float:
    """Extract liquidity factor from a candidate. Accepts dict (tests)
    or dataclass with avg_volume_20d attribute (production Candidate)."""
    if isinstance(c, dict):
        if "lf" in c:
            return float(c["lf"])
        return liquidity_factor(float(c["avg_volume_20d"]))
    return liquidity_factor(float(c.avg_volume_20d))


def _candidate_pattern(c) -> str:
    return c["pattern"] if isinstance(c, dict) else c.pattern


def _candidate_composite(c) -> float:
    return float(c["composite"] if isinstance(c, dict) else c.composite)


def _candidate_turnover(c) -> float:
    if isinstance(c, dict):
        return float(c["close"]) * float(c["avg_volume_20d"])
    return float(c.close) * float(c.avg_volume_20d)


def _candidate_id(c) -> str:
    return str(c["stock_id"] if isinstance(c, dict) else c.stock_id)


def apply_in_bucket_sort(candidates: list) -> list:
    """Sort candidates by `(in_bucket, composite, turnover, stock_id)`.

    Primary key: in_bucket bool (True ranks first).
    Secondary: composite_score desc within tier.
    Tiebreak: close × avg_volume_20d desc, then stock_id asc for stability.

    Patterns absent from SWEET_SPOTS (rectangle, unknown) treat every
    candidate as out-of-bucket → effectively composite-only sort.
    """
    def key(c):
        in_bucket = is_in_sweet_spot(_candidate_pattern(c), _candidate_lf(c))
        return (
            not in_bucket,  # False sorts before True → in_bucket first
            -_candidate_composite(c),
            -_candidate_turnover(c),
            _candidate_id(c),
        )
    return sorted(candidates, key=key)
