"""Pure win-probability calibration metrics.

Production-side calibration core (no numpy, no DB, no HTTP) shared by the
/api/calibration endpoint and scripts/validate_production_calibration.py.
Distinct from scripts/_calibration.py, which is an offline numpy helper for
validate_elo.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CalibrationBucket:
    lo: float
    hi: float
    predicted_mean: float
    actual_rate: float
    count: int


@dataclass
class CalibrationResult:
    buckets: list[CalibrationBucket]
    brier: float
    n: int


def compute_calibration(
    predictions: list[tuple[float | None, bool]], n_buckets: int = 10
) -> CalibrationResult:
    """Bucket predicted probabilities against actual outcomes.

    `predictions` is a list of (predicted_prob_in_0_1, outcome_bool). Entries
    with a None probability are dropped. Returns reliability buckets, the
    overall Brier score, and N (after dropping Nones).
    """
    preds = [(float(p), bool(o)) for p, o in predictions if p is not None]
    n = len(preds)
    if n == 0:
        return CalibrationResult(buckets=[], brier=0.0, n=0)

    brier = sum((p - (1.0 if o else 0.0)) ** 2 for p, o in preds) / n

    buckets: list[CalibrationBucket] = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        if i < n_buckets - 1:
            members = [(p, o) for p, o in preds if lo <= p < hi]
        else:  # last bucket includes the right edge so 1.0 isn't dropped
            members = [(p, o) for p, o in preds if lo <= p <= hi]
        if not members:
            continue
        c = len(members)
        buckets.append(
            CalibrationBucket(
                lo=lo,
                hi=hi,
                predicted_mean=sum(p for p, _ in members) / c,
                actual_rate=sum(1 for _, o in members if o) / c,
                count=c,
            )
        )
    return CalibrationResult(buckets=buckets, brier=brier, n=n)
