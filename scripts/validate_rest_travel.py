#!/usr/bin/env python3
"""Investigate whether a rest/travel term improves the Elo win-probability model.

Estimates the effect controlling for Elo (numpy IRLS logistic), converts the
coefficients to Elo points, replays the season WITH that adjustment, and reports
whether the held-out Brier improvement clears its paired-bootstrap CI.

Ships no model change — it prints a SHIP/NULL verdict. Run from repo root, venv
active:  python -m scripts.validate_rest_travel

Calibration baseline: 2016–present skip 2020 (reuses validate_elo._SEASONS).
"""

from __future__ import annotations

import numpy as np

from scripts._calibration import (
    bootstrap_brier_delta_ci,
    brier_score,
    log_loss,
)
from scripts._logistic import logistic_fit
from scripts.validate_elo import _EVAL_START, _fetch_all_games
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    expected_win_prob,
    replay_games,
)
from src.scoring.rest_travel import (
    assert_all_teams_have_coords,
    compute_rest_travel_features,
)


def build_design_row(x_elo: float, feat: dict) -> list[float]:
    """One design-matrix row: [x_elo, rest_diff, b2b_diff, travel_diff_k]."""
    rest_a = feat["rest_a"] if feat["rest_a"] is not None else 0
    rest_b = feat["rest_b"] if feat["rest_b"] is not None else 0
    rest_diff = rest_a - rest_b
    b2b_diff = feat["b2b_a"] - feat["b2b_b"]
    travel_diff_k = (feat["travel_a"] - feat["travel_b"]) / 1000.0
    return [x_elo, rest_diff, b2b_diff, travel_diff_k]


def coef_to_elo_points(b_feature: float, b_elo: float) -> float:
    """Convert a logit coefficient to Elo points per unit: b_feature / b_elo."""
    return b_feature / b_elo


def main() -> None:
    print("=== Rest/Travel Win-Probability Investigation ===\n")
    print("Fetching historical games...")
    games = _fetch_all_games()
    if not games:
        print("No games — aborting.")
        return
    assert_all_teams_have_coords(games)
    print(f"Total: {len(games)} games\n")

    # Baseline replay (HCA on, no rest/travel).
    base = replay_games(
        games, k=DEFAULT_K, home_advantage=DEFAULT_HOME_ADVANTAGE, use_mov=True
    )
    # history excludes winner-less games; recompute features over the same
    # winner-filtered, identically-sorted list so zip() aligns element-for-element.
    feats_hist = compute_rest_travel_features(
        sorted(
            (g for g in games if g.get("winner_team")),
            key=lambda g: (g.get("date", ""), g.get("event_id", "")),
        )
    )

    rows: list[list[float]] = []
    outcomes: list[float] = []
    base_probs: list[float] = []
    for h, feat in zip(base.history, feats_hist):
        if h["date"] < _EVAL_START:
            continue
        x_elo = h["pre_a"] - h["pre_b"] + h["home_adv"]
        rows.append(build_design_row(x_elo, feat))
        outcomes.append(1.0 if h["winner"] == h["team_a"] else 0.0)
        base_probs.append(expected_win_prob(h["pre_a"], h["pre_b"], h["home_adv"]))

    x = np.array(rows)
    y = np.array(outcomes)
    res = logistic_fit(x, y)

    names = ["intercept", "x_elo", "rest_diff", "b2b_diff", "travel_diff_k"]
    print("Logistic fit (controlling for Elo):")
    for i, nm in enumerate(names):
        print(
            f"  {nm:<14} coef={res.coef[i]:+.5f}  se={res.stderr[i]:.5f}  "
            f"p={res.pvalues[i]:.4g}"
        )

    b_elo = res.coef[1]
    elo_rest = coef_to_elo_points(res.coef[2], b_elo)
    elo_b2b = coef_to_elo_points(res.coef[3], b_elo)
    elo_travel_per_1000 = coef_to_elo_points(res.coef[4], b_elo)
    print("\nElo-point magnitudes (per unit):")
    print(f"  rest_day_diff:   {elo_rest:+.1f} Elo")
    print(f"  b2b_diff:        {elo_b2b:+.1f} Elo")
    print(f"  travel/1000mi:   {elo_travel_per_1000:+.1f} Elo")

    def adjust(feat: dict) -> float:
        rest_a = feat["rest_a"] if feat["rest_a"] is not None else 0
        rest_b = feat["rest_b"] if feat["rest_b"] is not None else 0
        return (
            elo_rest * (rest_a - rest_b)
            + elo_b2b * (feat["b2b_a"] - feat["b2b_b"])
            + elo_travel_per_1000 * (feat["travel_a"] - feat["travel_b"]) / 1000.0
        )

    adj = replay_games(
        games,
        k=DEFAULT_K,
        home_advantage=DEFAULT_HOME_ADVANTAGE,
        use_mov=True,
        rest_travel_adjust=adjust,
    )
    adj_probs: list[float] = []
    for h in adj.history:
        if h["date"] < _EVAL_START:
            continue
        adj_probs.append(expected_win_prob(h["pre_a"], h["pre_b"], h["home_adv"]))

    base_arr, adj_arr, y_arr = (
        np.array(base_probs),
        np.array(adj_probs),
        np.array(outcomes),
    )
    print("\nHeld-out scoring:")
    print(
        f"  baseline  Brier={brier_score(base_arr, y_arr):.5f}  "
        f"LogLoss={log_loss(base_arr, y_arr):.5f}"
    )
    print(
        f"  adjusted  Brier={brier_score(adj_arr, y_arr):.5f}  "
        f"LogLoss={log_loss(adj_arr, y_arr):.5f}"
    )

    mean_d, lo, hi = bootstrap_brier_delta_ci(base_arr, adj_arr, y_arr, n_boot=2000)
    print("\nBootstrap Brier improvement (baseline - adjusted):")
    print(f"  mean={mean_d:+.6f}  95% CI=[{lo:+.6f}, {hi:+.6f}]")
    verdict = "SHIP" if lo > 0 else "NULL (within noise)"
    print(f"\nVERDICT: {verdict}")
    if lo > 0:
        print("  -> proceed to Plan 2 (integration) with the magnitudes above.")
    else:
        print("  -> document the null; no model change. Stop.")


if __name__ == "__main__":
    main()
