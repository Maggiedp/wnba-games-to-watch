#!/usr/bin/env python3
"""Validate Elo win-probability calibration against historical WNBA results.

Unlike BPI validation (which had look-ahead bias — we only have end-of-season
BPI), Elo is time-honest by construction: we replay games chronologically and
score each prediction using the ratings as they were BEFORE that game.

Grid-searches over K (responsiveness) and H (home-court Elo bonus) jointly,
since 2025 calibration showed a ~10-pt home-team win-rate bias in toss-up
games that pure ratings don't capture. Season-boundary regression toward the
mean is held fixed at DEFAULT_SEASON_REGRESSION; both grid and headline runs
use it so K/H are tuned for the system as deployed.

Calibration baseline: 2016–present, skip 2020. 2016 is warm-up (all teams
enter at 1500); predictions are evaluated from 2017 onward (~1500+ games vs
the prior ~574-game 2024–2025-only run).

Run from the repo root with the venv active:
    python -m scripts.validate_elo
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np

from scripts._calibration import brier_score, calibration_table, log_loss
from src.data.espn_api import fetch_games_for_range
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    DEFAULT_SEASON_REGRESSION,
    expected_win_prob,
    replay_games,
)

# Season windows. WNBA regular season runs roughly mid-May through September;
# playoffs extend into October. Use generous windows so we catch everything.
# 2020 is excluded: COVID bubble at a neutral site in Bradenton — applying
# DEFAULT_HOME_ADVANTAGE to those games would systematically distort ratings.
_SEASONS = [
    (date(y, 5, 1), date(y, 10, 31), str(y))
    for y in [2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025]
]
# Use 2016 as warm-up; evaluate from 2017 onward (~1500+ games).
_EVAL_START = "2017-01-01"


def _fetch_all_games() -> list[dict]:
    """Pull games across all configured seasons, filter to completed regular/postseason.

    Fetches seasons in parallel (network-bound) then merges in chronological order.
    """

    def _fetch_one(season: tuple[date, date, str]) -> tuple[str, list[dict]]:
        start, end, label = season
        finished = [
            g
            for g in fetch_games_for_range(start, end)
            if g["winner_team"] and g.get("season_type", 2) != 1
        ]
        print(f"  {label}: {len(finished)} completed regular/postseason games")
        return label, finished

    with ThreadPoolExecutor(max_workers=len(_SEASONS)) as executor:
        # executor.map preserves _SEASONS order, so merge is chronological.
        results = list(executor.map(_fetch_one, _SEASONS))

    return [g for _, season_games in results for g in season_games]


def _replay_and_score(
    games: list[dict],
    k: float,
    home_advantage: float,
    eval_start: str,
    season_regression: float = DEFAULT_SEASON_REGRESSION,
    use_mov: bool = False,
    presorted: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Replay games, return (pre-game win probs, outcomes, final ratings)."""
    replay = replay_games(
        games,
        k=k,
        home_advantage=home_advantage,
        season_regression=season_regression,
        use_mov=use_mov,
        presorted=presorted,
    )
    probs: list[float] = []
    outcomes: list[float] = []
    for h in replay.history:
        if h["date"] < eval_start:
            continue
        p_a = expected_win_prob(h["pre_a"], h["pre_b"], home_advantage)
        probs.append(p_a)
        outcomes.append(1.0 if h["winner"] == h["team_a"] else 0.0)
    return np.array(probs), np.array(outcomes), replay.final_ratings


def _grid_search_k_h(
    games: list[dict],
    eval_start: str,
    k_candidates: np.ndarray,
    h_candidates: np.ndarray,
    use_mov: bool = False,
) -> tuple[
    tuple[float, float, float, float], dict[tuple[float, float], tuple[float, float]]
]:
    """Joint K×H grid search.

    Returns ((best_k, best_h, best_ll, best_brier), {(k, h): (ll, brier)})
    """
    grid: dict[tuple[float, float], tuple[float, float]] = {}
    best = None
    for k in k_candidates:
        for h in h_candidates:
            probs, outcomes, _ = _replay_and_score(
                games, float(k), float(h), eval_start, use_mov=use_mov, presorted=True
            )
            if len(probs) == 0:
                continue
            ll = log_loss(probs, outcomes)
            br = brier_score(probs, outcomes)
            grid[(float(k), float(h))] = (ll, br)
            if best is None or ll < best[2]:
                best = (float(k), float(h), ll, br)
    assert best is not None, "Grid search returned no results"
    return best, grid


def _print_metrics(label: str, probs: np.ndarray, outcomes: np.ndarray) -> None:
    n = len(outcomes)
    brier = brier_score(probs, outcomes)
    ll = log_loss(probs, outcomes)
    acc = float(np.mean((probs > 0.5) == (outcomes == 1)))
    print(f"  {label}: N={n}  Brier={brier:.4f}  LogLoss={ll:.4f}  PickAcc={acc:.1%}")


def _print_calibration(label: str, probs: np.ndarray, outcomes: np.ndarray) -> None:
    print(f"\nCalibration ({label}):")
    print(f"  {'Bucket':<12} {'N':>5} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
    for row in calibration_table(probs, outcomes):
        err = row["actual"] - row["avg_pred"]
        print(
            f"  {row['bucket']:<12} {row['n']:>5} {row['avg_pred']:>10.1%} "
            f"{row['actual']:>10.1%} {err:>+8.1%}"
        )


def main() -> None:
    print("=== Elo Win-Probability Calibration Validation ===\n")

    print("Fetching historical WNBA games...")
    games = _fetch_all_games()
    if not games:
        print("No games found — aborting.")
        return
    print(f"Total: {len(games)} games across {len(_SEASONS)} seasons\n")

    # Sort once here; pass presorted=True to every replay to skip redundant sorts.
    games.sort(key=lambda g: (g.get("date", ""), g.get("event_id", "")))

    baseline_brier = 0.25
    baseline_ll = math.log(2)
    print(f"Baselines: random Brier={baseline_brier:.4f}, LogLoss={baseline_ll:.4f}")
    print(
        f"Season regression toward mean held fixed at "
        f"{DEFAULT_SEASON_REGRESSION:.3f} for grid + headline runs.\n"
    )

    print("Headline comparison:")
    probs_neutral, outcomes, _ = _replay_and_score(
        games, DEFAULT_K, 0.0, _EVAL_START, presorted=True
    )
    _print_metrics(f"Neutral Elo (K={DEFAULT_K}, H=0)", probs_neutral, outcomes)

    probs_no_reg, _, _ = _replay_and_score(
        games,
        DEFAULT_K,
        DEFAULT_HOME_ADVANTAGE,
        _EVAL_START,
        season_regression=0.0,
        presorted=True,
    )
    _print_metrics(
        f"No regression  (K={DEFAULT_K}, H={DEFAULT_HOME_ADVANTAGE}, reg=0)",
        probs_no_reg,
        outcomes,
    )

    probs_default, _, _ = _replay_and_score(
        games, DEFAULT_K, DEFAULT_HOME_ADVANTAGE, _EVAL_START, presorted=True
    )
    _print_metrics(
        f"Defaults       (K={DEFAULT_K}, H={DEFAULT_HOME_ADVANTAGE}, "
        f"reg={DEFAULT_SEASON_REGRESSION:.3f})",
        probs_default,
        outcomes,
    )

    probs_mov, _, _ = _replay_and_score(
        games,
        DEFAULT_K,
        DEFAULT_HOME_ADVANTAGE,
        _EVAL_START,
        use_mov=True,
        presorted=True,
    )
    _print_metrics(
        f"Defaults + MOV (K={DEFAULT_K}, H={DEFAULT_HOME_ADVANTAGE}, mov=on)",
        probs_mov,
        outcomes,
    )

    print("\nRunning joint K×H grid search (no MOV)...")
    k_candidates = np.arange(10.0, 51.0, 2.0)
    h_candidates = np.arange(0.0, 151.0, 5.0)
    (best_k, best_h, best_ll, best_br), grid = _grid_search_k_h(
        games, _EVAL_START, k_candidates, h_candidates
    )
    print(
        f"  Best: K={best_k:.0f}, H={best_h:.0f} → LogLoss={best_ll:.4f}, Brier={best_br:.4f}"
    )

    print("\nAt best (K, H):")
    probs_best, _, ratings_best = _replay_and_score(
        games, best_k, best_h, _EVAL_START, presorted=True
    )
    _print_metrics(f"Elo (K={best_k:.0f}, H={best_h:.0f})", probs_best, outcomes)
    _print_calibration(f"K={best_k:.0f}, H={best_h:.0f}", probs_best, outcomes)

    print("\nRunning joint K×H grid search (MOV on)...")
    (mov_k, mov_h, mov_ll, mov_br), _ = _grid_search_k_h(
        games, _EVAL_START, k_candidates, h_candidates, use_mov=True
    )
    print(
        f"  Best with MOV: K={mov_k:.0f}, H={mov_h:.0f} → "
        f"LogLoss={mov_ll:.4f}, Brier={mov_br:.4f}"
    )
    probs_mov_best, _, _ = _replay_and_score(
        games, mov_k, mov_h, _EVAL_START, use_mov=True, presorted=True
    )
    _print_metrics(f"MOV (K={mov_k:.0f}, H={mov_h:.0f})", probs_mov_best, outcomes)
    _print_calibration(f"MOV K={mov_k:.0f}, H={mov_h:.0f}", probs_mov_best, outcomes)
    print(
        f"\n  MOV vs no-MOV at their respective optima: "
        f"ΔLogLoss={mov_ll - best_ll:+.4f}, ΔBrier={mov_br - best_br:+.4f}  "
        f"(negative = MOV better)"
    )

    _print_calibration(f"K={DEFAULT_K}, H=0 (neutral)", probs_neutral, outcomes)

    print(f"\nH sensitivity at K={DEFAULT_K}:")
    best_entry_at_default_k = min(
        (v for (k, _), v in grid.items() if k == DEFAULT_K),
        key=lambda v: v[0],
        default=None,
    )
    for h in (0, 20, 40, 60, 70, 80, 100, 120, 150):
        entry = grid.get((DEFAULT_K, float(h)))
        if entry:
            marker = " ← best-for-this-K" if entry == best_entry_at_default_k else ""
            print(f"  H={h:>3}: LogLoss={entry[0]:.4f}, Brier={entry[1]:.4f}{marker}")

    print(f"\nK sensitivity at H={best_h:.0f}:")
    for k in (10, 14, 18, 20, 22, 26, 30, 40, 50):
        entry = grid.get((float(k), best_h))
        if entry:
            marker = " ← best" if float(k) == best_k else ""
            print(f"  K={k:>2}: LogLoss={entry[0]:.4f}, Brier={entry[1]:.4f}{marker}")

    print(
        f"\nFinal ratings at end of replay (top 6 / bottom 6) at K={best_k:.0f}, H={best_h:.0f}:"
    )
    ranked = sorted(ratings_best.items(), key=lambda x: -x[1])
    for name, r in ranked[:6]:
        print(f"  {name:<30} {r:>7.1f}")
    print("  ...")
    for name, r in ranked[-6:]:
        print(f"  {name:<30} {r:>7.1f}")

    if ranked:
        best_r = ranked[0][1]
        worst_r = ranked[-1][1]
        spread_wp = expected_win_prob(best_r, worst_r)
        print(
            f"\n  Elo spread (best vs worst, Δ={best_r - worst_r:.0f}): "
            f"{spread_wp:.1%} win probability at neutral site"
        )

    print("\nConclusion:")
    # Compare the MOV-on optimum against defaults — that's the deployed config.
    if abs(mov_k - DEFAULT_K) <= 5.0 and abs(mov_h - DEFAULT_HOME_ADVANTAGE) <= 5.0:
        print(
            f"  Current defaults (K={DEFAULT_K}, H={DEFAULT_HOME_ADVANTAGE}, "
            f"reg={DEFAULT_SEASON_REGRESSION:.3f}, mov=on) are within the flat "
            f"region of the loss surface. MOV optimum: K={mov_k:.0f}, H={mov_h:.0f}."
        )
    else:
        print(
            f"  MOV optimum (K={mov_k:.0f}, H={mov_h:.0f}) differs from defaults "
            f"(K={DEFAULT_K}, H={DEFAULT_HOME_ADVANTAGE}) by more than the flat "
            "region. Consider updating DEFAULT_K / DEFAULT_HOME_ADVANTAGE in elo.py."
        )


if __name__ == "__main__":
    main()
