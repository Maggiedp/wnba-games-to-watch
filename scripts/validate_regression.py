#!/usr/bin/env python3
"""Validate season regression calibration for Elo ratings.

Tests how well post-regression Elo predicts early next-season game outcomes
across multiple historical season boundaries. The optimal regression value
minimizes per-game Brier score on those early games.

Calibration baseline: 2016–present, skip 2020 (COVID bubble — all games were
played at a neutral site in Bradenton, FL, so applying DEFAULT_HOME_ADVANTAGE
to 2020 games would distort ratings). See CLAUDE.md § Priority 7.

Why per-game Brier score instead of playoff odds:
  Playoff probabilities are downstream of win probabilities. If per-game
  predictions are well-calibrated at season transitions, playoff odds flow
  from that. Per-game Brier also gives ~40 data points per boundary instead
  of the binary "made playoffs / didn't" signal.

Run from the repo root with the venv active:
    python -m scripts.validate_regression
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from scripts._calibration import brier_score, calibration_table
from src.data.espn_api import fetch_games_for_range
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    DEFAULT_SEASON_REGRESSION,
    INITIAL_RATING,
    expected_win_prob,
    replay_games,
)

# Calibration range: 2016–present, skip 2020.
_CALIBRATION_START = 2016
_SKIP_SEASONS = {2020}
# First N days of a new season to use as the test window (~4 weeks).
_EARLY_SEASON_DAYS = 28

_REGRESSION_CANDIDATES = [0.0, 0.1, 0.2, 1 / 3, 0.5, 2 / 3, 1.0]


def _apply_regression(ratings: dict[str, float], factor: float) -> dict[str, float]:
    """Pull each rating toward INITIAL_RATING by `factor` (same formula as elo.py)."""
    if factor <= 0:
        return dict(ratings)
    keep = 1.0 - factor
    return {name: INITIAL_RATING + (r - INITIAL_RATING) * keep for name, r in ratings.items()}


def _fetch_seasons() -> dict[int, list[dict]]:
    """Fetch completed regular-season games for each season in the calibration range."""
    print("Fetching game data from ESPN...")
    games_by_year: dict[int, list[dict]] = {}
    for year in range(_CALIBRATION_START, date.today().year + 1):
        if year in _SKIP_SEASONS:
            continue
        raw = fetch_games_for_range(date(year, 5, 1), date(year, 9, 30))
        completed = [
            g for g in raw if g.get("winner_team") and g.get("season_type", 2) != 1
        ]
        if completed:
            games_by_year[year] = completed
            print(f"  {year}: {len(completed)} completed regular-season games")
    return games_by_year


def _test_boundary(
    prior_years: list[int],
    idx: int,
    next_year: int,
    games_by_year: dict[int, list[dict]],
    regression: float,
) -> tuple[list[float], list[float]] | None:
    """Return (predicted_probs, outcomes) for early next_year games.

    Uses cumulative history from _CALIBRATION_START through prior_year.
    NOTE: the 2019→2021 year gap (skipping 2020) applies regression once
    rather than twice — an acceptable approximation at this sample size.
    """
    prior_year = prior_years[idx]

    # Cumulative training: all seasons from CALIBRATION_START through prior_year.
    training: list[dict] = []
    for y in prior_years[: idx + 1]:
        training.extend(games_by_year[y])
    training.sort(key=lambda g: (g.get("date", ""), g.get("event_id", "")))

    # Replay with candidate regression — applies regression at each detected
    # year boundary (e.g. 2016→2017, 2017→2018, …). Final ratings are
    # end-of-prior_year, not yet regressed for the upcoming boundary.
    result = replay_games(
        training,
        home_advantage=DEFAULT_HOME_ADVANTAGE,
        season_regression=regression,
        use_mov=True,
    )
    ratings = dict(result.final_ratings)

    # Apply regression at the boundary we're testing (prior_year → next_year).
    regressed = _apply_regression(ratings, regression)

    # First EARLY_SEASON_DAYS of next_year regular season.
    next_games = games_by_year.get(next_year, [])
    if not next_games:
        return None
    season_start = min(g["date"] for g in next_games)
    cutoff = (
        date.fromisoformat(season_start) + timedelta(days=_EARLY_SEASON_DAYS)
    ).isoformat()
    early = [g for g in next_games if g["date"] <= cutoff]
    if not early:
        return None

    probs, outcomes = [], []
    for game in early:
        elo_a = regressed.get(game["team_a"], INITIAL_RATING)
        elo_b = regressed.get(game["team_b"], INITIAL_RATING)
        pred = expected_win_prob(elo_a, elo_b, home_advantage=DEFAULT_HOME_ADVANTAGE)
        actual = 1.0 if game["winner_team"] == game["team_a"] else 0.0
        probs.append(pred)
        outcomes.append(actual)

    return probs, outcomes


def main() -> None:
    print("=== Season Regression Calibration Validation ===\n")

    games_by_year = _fetch_seasons()

    # Build boundary list: (index-into-prior_years, prior_year, next_year)
    all_years = sorted(games_by_year)
    prior_years = [y for y in all_years if y < max(all_years)]
    boundaries = [
        (i, y, y + 1)
        for i, y in enumerate(prior_years)
        if y + 1 not in _SKIP_SEASONS and y + 1 in games_by_year
    ]
    print(
        f"\nTesting {len(boundaries)} season boundaries: "
        + ", ".join(f"{a}→{b}" for _, a, b in boundaries)
    )
    print(f"Early-season window: first {_EARLY_SEASON_DAYS} days of new season")
    print(
        f"Replay params: K={DEFAULT_K}, H={DEFAULT_HOME_ADVANTAGE}, MOV=on "
        f"(regression varied)\n"
    )

    # ── Run validation ───────────────────────────────────────────────────────
    rows = []
    boundary_briers: dict[str, list[float]] = {}  # label → brier per regression

    for regression in _REGRESSION_CANDIDATES:
        all_probs: list[float] = []
        all_outcomes: list[float] = []
        per_boundary: dict[str, float] = {}

        for idx, prior_year, next_year in boundaries:
            label = f"{prior_year}→{next_year}"
            result = _test_boundary(
                prior_years, idx, next_year, games_by_year, regression
            )
            if result is None:
                continue
            probs, outcomes = result
            all_probs.extend(probs)
            all_outcomes.extend(outcomes)
            per_boundary[label] = brier_score(np.array(probs), np.array(outcomes))

        if not all_probs:
            continue

        arr_p, arr_o = np.array(all_probs), np.array(all_outcomes)
        overall = brier_score(arr_p, arr_o)
        rows.append((regression, overall, len(all_probs), per_boundary, arr_p, arr_o))
        for label, b in per_boundary.items():
            boundary_briers.setdefault(label, []).append(b)

        marker = " ← current default" if abs(regression - DEFAULT_SEASON_REGRESSION) < 1e-6 else ""
        print(f"regression={regression:.3f}  brier={overall:.4f}  n={len(all_probs)}{marker}")

    if not rows:
        print("No results — check data fetch.")
        return

    # ── Summary table ────────────────────────────────────────────────────────
    best_brier = min(r[1] for r in rows)
    print("\n" + "=" * 65)
    print("RESULTS SUMMARY")
    print("=" * 65)
    print(f"\n{'Regression':>12}  {'Brier':>8}  {'N':>6}  {'':10}")
    for regression, overall, n, _, _, _ in rows:
        is_best = abs(overall - best_brier) < 1e-6
        is_default = abs(regression - DEFAULT_SEASON_REGRESSION) < 1e-6
        tag = ("← best" if is_best else "") + (" (default)" if is_default else "")
        print(f"{regression:12.4f}  {overall:.4f}  {n:6d}  {tag}")

    # Per-boundary breakdown
    print("\nPer-boundary Brier score:")
    all_labels = [f"{a}→{b}" for _, a, b in boundaries if f"{a}→{b}" in boundary_briers]
    header = f"{'Boundary':>12}" + "".join(
        f"  {r[0]:.2f}" for r in rows
    )
    print(header)
    for label in all_labels:
        row_str = f"{label:>12}"
        for regression, _, _, per_boundary, _, _ in rows:
            val = per_boundary.get(label)
            row_str += f"  {val:.4f}" if val is not None else "      —"
        print(row_str)

    # Calibration for best and default
    best_row = min(rows, key=lambda r: r[1])
    default_row = next(
        (r for r in rows if abs(r[0] - DEFAULT_SEASON_REGRESSION) < 1e-6), None
    )


    print(f"\nCalibration at best regression ({best_row[0]:.3f}):")
    print(f"  {'Bucket':<12} {'N':>5} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
    for row in calibration_table(np.array(best_row[4]), np.array(best_row[5])):
        err = row["actual"] - row["avg_pred"]
        print(
            f"  {row['bucket']:<12} {row['n']:>5} {row['avg_pred']:>10.1%} "
            f"{row['actual']:>10.1%} {err:>+8.1%}"
        )

    if default_row and abs(default_row[0] - best_row[0]) > 1e-6:
        print(f"\nCalibration at current default ({DEFAULT_SEASON_REGRESSION:.3f}):")
        print(f"  {'Bucket':<12} {'N':>5} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
        for row in calibration_table(np.array(default_row[4]), np.array(default_row[5])):
            err = row["actual"] - row["avg_pred"]
            print(
                f"  {row['bucket']:<12} {row['n']:>5} {row['avg_pred']:>10.1%} "
                f"{row['actual']:>10.1%} {err:>+8.1%}"
            )

    # ── Conclusion ───────────────────────────────────────────────────────────
    print("\nConclusion:")
    delta = best_row[1] - (default_row[1] if default_row else best_row[1])
    if abs(best_row[0] - DEFAULT_SEASON_REGRESSION) < 0.05:
        print(
            f"  Current default ({DEFAULT_SEASON_REGRESSION:.3f}) is near the optimum. "
            f"No change recommended."
        )
    else:
        print(
            f"  Optimal regression: {best_row[0]:.3f} (Brier={best_row[1]:.4f})"
        )
        if default_row:
            print(
                f"  Current default:    {DEFAULT_SEASON_REGRESSION:.3f} "
                f"(Brier={default_row[1]:.4f}, Δ={default_row[1] - best_row[1]:+.4f})"
            )
        print(
            "  If difference is meaningful (>0.001), update DEFAULT_SEASON_REGRESSION "
            "in src/scoring/elo.py and re-run scripts/validate_elo.py to check "
            "that K/H optima are stable."
        )


if __name__ == "__main__":
    main()
