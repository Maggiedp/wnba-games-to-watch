"""Shared calibration metrics for win-probability validation scripts.

Used by validate_elo.py. Kept in scripts/ (not src/)
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


def bootstrap_brier_delta_ci(
    probs_baseline: np.ndarray,
    probs_adjusted: np.ndarray,
    outcomes: np.ndarray,
    n_boot: int = 1000,
    seed: int = 0,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """Paired bootstrap CI of the Brier improvement (baseline - adjusted).

    Resamples GAME INDICES with replacement (paired: the same indices score both
    models each draw, preserving per-game correlation). Positive = adjusted model
    has the lower (better) Brier. Returns (mean_delta, ci_low, ci_high).
    """
    probs_baseline = np.asarray(probs_baseline, dtype=float)
    probs_adjusted = np.asarray(probs_adjusted, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    n = len(outcomes)
    se_base = (probs_baseline - outcomes) ** 2
    se_adj = (probs_adjusted - outcomes) ** 2

    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        deltas[i] = se_base[idx].mean() - se_adj[idx].mean()

    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(deltas, [alpha, 1.0 - alpha])
    return float(deltas.mean()), float(lo), float(hi)
