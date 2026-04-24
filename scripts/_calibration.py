"""Shared calibration metrics for win-probability validation scripts.

Used by validate_bpi.py and validate_elo.py. Kept in scripts/ (not src/)
because these are offline analysis helpers, not production code.
"""

from __future__ import annotations

import numpy as np


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    return float(np.mean((probs - outcomes) ** 2))


def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    eps = 1e-7
    return float(
        -np.mean(
            outcomes * np.log(probs + eps) + (1 - outcomes) * np.log(1 - probs + eps)
        )
    )


def calibration_table(
    probs: np.ndarray, outcomes: np.ndarray, n_buckets: int = 5
) -> list[dict]:
    rows = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        mask = (probs >= lo) & (probs < hi) if i < n_buckets - 1 else (probs >= lo)
        if not mask.any():
            continue
        rows.append(
            {
                "bucket": f"{lo:.0%}–{hi:.0%}",
                "n": int(mask.sum()),
                "avg_pred": float(probs[mask].mean()),
                "actual": float(outcomes[mask].mean()),
            }
        )
    return rows
