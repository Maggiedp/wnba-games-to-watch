#!/usr/bin/env python3
"""Validate BPI win-probability calibration against 2025 WNBA results.

Uses end-of-2025-season BPI as a proxy for in-game BPI (the best we can do
without historical snapshots). Metrics: Brier score, log loss, calibration
table, and an optimal-k search.

Run from the repo root with the venv active:
    python -m scripts.validate_bpi
"""

import math
from datetime import date

import numpy as np

from scripts._calibration import brier_score, calibration_table, log_loss
from src.data.espn_api import fetch_bpi_ratings, fetch_games_for_range


def _find_optimal_k(bpi_diffs: np.ndarray, outcomes: np.ndarray) -> float:
    """Vectorized grid search over k=0.001..0.300 to minimize log loss."""
    k_values = np.arange(0.001, 0.301, 0.001)  # shape (300,)
    # probs shape: (300, N) — one row per k candidate
    exponents = -k_values[:, None] * bpi_diffs[None, :] * np.log(10)
    probs = 1.0 / (1.0 + np.exp(exponents))
    eps = 1e-7
    losses = -np.mean(
        outcomes * np.log(probs + eps) + (1 - outcomes) * np.log(1 - probs + eps),
        axis=1,
    )
    return float(k_values[np.argmin(losses)])


def main() -> None:
    print("=== BPI Win-Probability Calibration Validation ===\n")

    print("Fetching BPI ratings (2025/2026 season)...")
    bpi = fetch_bpi_ratings()
    bpi_vals = sorted(bpi.items(), key=lambda x: -x[1])
    print(
        f"Got {len(bpi)} teams. Range: {bpi_vals[-1][1]:.2f} to {bpi_vals[0][1]:.2f}\n"
    )

    print("Fetching 2025 WNBA completed games (May–October)...")
    all_games = fetch_games_for_range(date(2025, 5, 1), date(2025, 10, 31))
    games = [g for g in all_games if g["winner_team"] and g.get("season_type", 2) != 1]
    print(f"Found {len(games)} completed regular/post-season games\n")

    diffs, outcomes = [], []
    skipped = 0
    for g in games:
        ta, tb = g["team_a"], g["team_b"]
        if ta not in bpi or tb not in bpi:
            skipped += 1
            continue
        diffs.append(bpi[ta] - bpi[tb])
        outcomes.append(1.0 if g["winner_team"] == ta else 0.0)

    if not diffs:
        print("No games matched BPI data — nothing to evaluate.")
        return

    if skipped:
        print(
            f"Skipped {skipped} games (teams missing from BPI, e.g. expansion teams)\n"
        )

    bpi_diffs = np.array(diffs)
    outcomes_arr = np.array(outcomes)

    K_OLD = 0.04
    K_CURRENT = 0.08

    def _probs(k: float) -> np.ndarray:
        return 1.0 / (1.0 + np.power(10, -k * bpi_diffs))

    probs_old = _probs(K_OLD)
    probs_current = _probs(K_CURRENT)

    brier_old = brier_score(probs_old, outcomes_arr)
    ll_old = log_loss(probs_old, outcomes_arr)
    brier_current = brier_score(probs_current, outcomes_arr)
    ll_current = log_loss(probs_current, outcomes_arr)

    n = len(outcomes_arr)
    baseline_brier = 0.25
    baseline_ll = math.log(2)

    print(f"Evaluated on {n} games\n")
    print(
        f"{'Metric':<20} {f'k={K_OLD} (old)':>16} {f'k={K_CURRENT} (current)':>18} {'Random baseline':>16}"
    )
    print("-" * 72)
    print(
        f"{'Brier score':<20} {brier_old:>16.4f} {brier_current:>18.4f} {baseline_brier:>16.4f}"
    )
    print(f"{'Log loss':<20} {ll_old:>16.4f} {ll_current:>18.4f} {baseline_ll:>16.4f}")

    print(
        f"\nCalibration at k={K_CURRENT} (predicted win probability vs actual win rate):"
    )
    print(f"  {'Bucket':<12} {'N':>5} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
    for row in calibration_table(probs_current, outcomes_arr):
        err = row["actual"] - row["avg_pred"]
        print(
            f"  {row['bucket']:<12} {row['n']:>5} {row['avg_pred']:>10.1%} {row['actual']:>10.1%} {err:>+8.1%}"
        )

    print("\nSearching for optimal k (minimizing log loss)...")
    k_opt = _find_optimal_k(bpi_diffs, outcomes_arr)
    probs_opt = _probs(k_opt)
    brier_opt = brier_score(probs_opt, outcomes_arr)
    ll_opt = log_loss(probs_opt, outcomes_arr)

    print(f"  k={K_OLD:.3f} (old)     → log loss {ll_old:.4f}, Brier {brier_old:.4f}")
    print(
        f"  k={K_CURRENT:.3f} (current) → log loss {ll_current:.4f}, Brier {brier_current:.4f}"
    )
    print(f"  k={k_opt:.3f} (optimal) → log loss {ll_opt:.4f}, Brier {brier_opt:.4f}")

    swing_current = 1.0 / (1.0 + 10 ** (-K_CURRENT * 12))
    swing_opt = 1.0 / (1.0 + 10 ** (-k_opt * 12))
    print(
        f"\n  k={K_CURRENT:.3f}: best-vs-worst (ΔBPI=12) → {swing_current:.1%} win probability"
    )
    print(f"  k={k_opt:.3f}: best-vs-worst (ΔBPI=12) → {swing_opt:.1%} win probability")

    if abs(k_opt - K_CURRENT) < 0.005:
        print(f"\nConclusion: k={K_CURRENT} is well-calibrated for this data.")
    else:
        print(
            f"\nConclusion: optimal k={k_opt:.3f} differs from current k={K_CURRENT}."
            " Consider updating k in monte_carlo.py."
        )

    higher_bpi_won = float(np.mean((bpi_diffs > 0) == (outcomes_arr == 1)))
    print(f"\nHigher-BPI team won {higher_bpi_won:.1%} of games")
    print("  (50% = no predictive power, >55% = useful signal)")


if __name__ == "__main__":
    main()
