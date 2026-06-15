#!/usr/bin/env python3
"""Investigate whether a rest/travel term improves the Elo win-probability model.

Two distinct outputs, deliberately kept separate:

1. **In-sample coefficient estimate (descriptive).** A logistic fit on all
   evaluated games, controlling for the Elo differential, reports each rest/travel
   term's coefficient, standard error, and p-value. This answers "is the effect
   statistically detectable in the historical sample?" It is in-sample by
   construction and is NOT used to decide shipping.

2. **Out-of-sample ship gate (rolling origin).** For each test season, coefficients
   are fit ONLY on prior seasons, converted to Elo points, and applied at PREDICTION
   TIME (an additive term on the baseline-rating differential) to that season's
   games. Pooling these held-out predictions, the gate ships only if the
   paired-bootstrap CI of the Brier improvement over baseline lies entirely above 0.
   This is the honest test: coefficients never score the games they were fit on.

Ships no model change — it prints a SHIP/NULL verdict. Run from repo root, venv
active:  python -m scripts.validate_rest_travel

Calibration baseline: 2016–present skip 2020 (reuses validate_elo._SEASONS).
"""

from __future__ import annotations

import numpy as np

from scripts._calibration import bootstrap_brier_delta_ci, brier_score, log_loss
from scripts._logistic import LogisticResult, logistic_fit
from scripts.validate_elo import _EVAL_START, _fetch_all_games
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    expected_win_prob,
    is_replayable,
    replay_games,
)
from src.scoring.rest_travel import (
    assert_all_teams_have_coords,
    compute_rest_travel_features,
)

# A test season is scored out-of-sample only once at least this many earlier
# evaluated games exist to fit the coefficients on — keeps early rolling folds
# from being fit on a trivially small training set.
_MIN_TRAIN_GAMES = 500


def _rest_diff(feat: dict) -> int:
    """Rest-day differential (home − away), or 0 when either side is a season
    debut (rest is None). A debut team's rest is *unknown*, not zero — coercing
    None→0 would read as "tired" (and is sign-wrong for a well-rested opener), so
    we treat the differential as neutral instead."""
    if feat["rest_a"] is None or feat["rest_b"] is None:
        return 0
    return feat["rest_a"] - feat["rest_b"]


def build_design_row(x_elo: float, feat: dict) -> list[float]:
    """One design-matrix row: [x_elo, rest_diff, b2b_diff, travel_diff_k, tz_diff]."""
    b2b_diff = feat["b2b_a"] - feat["b2b_b"]
    travel_diff_k = (feat["travel_a"] - feat["travel_b"]) / 1000.0
    tz_diff = feat["tz_a"] - feat["tz_b"]
    return [x_elo, _rest_diff(feat), b2b_diff, travel_diff_k, tz_diff]


def coef_to_elo_points(b_feature: float, b_elo: float) -> float:
    """Convert a logit coefficient to Elo points per unit: b_feature / b_elo."""
    return b_feature / b_elo


def magnitudes_from_fit(res: LogisticResult) -> tuple[float, float, float, float]:
    """(elo_rest, elo_b2b, elo_travel_per_1000, elo_tz) from a fit. b_elo = coef[1]."""
    b_elo = res.coef[1]
    return (
        coef_to_elo_points(res.coef[2], b_elo),
        coef_to_elo_points(res.coef[3], b_elo),
        coef_to_elo_points(res.coef[4], b_elo),
        coef_to_elo_points(res.coef[5], b_elo),
    )


def main() -> None:
    print("=== Rest/Travel Win-Probability Investigation ===\n")
    print("Fetching historical games...")
    games = _fetch_all_games()
    if not games:
        print("No games — aborting.")
        return
    assert_all_teams_have_coords(games)
    print(f"Total: {len(games)} games\n")

    # Sort once and reuse: replay_games (presorted) and the feature stream both
    # process the SAME (date, event_id)-ordered, is_replayable-filtered games, so
    # base.history aligns positionally with `replayed`.
    ordered = sorted(games, key=lambda g: (g.get("date", ""), g.get("event_id", "")))
    replayed = [g for g in ordered if is_replayable(g)]
    base = replay_games(
        ordered,
        k=DEFAULT_K,
        home_advantage=DEFAULT_HOME_ADVANTAGE,
        use_mov=True,
        presorted=True,
    )
    feats_hist = compute_rest_travel_features(replayed)
    if len(replayed) != len(base.history):
        raise RuntimeError(
            f"feature/history length mismatch: {len(replayed)} vs {len(base.history)}"
        )

    # Per-game evaluation records (>= _EVAL_START). Fail loud on any per-row
    # misalignment rather than trusting positional zip. (Both streams share the
    # is_replayable predicate + sort, so this guards only against future drift.)
    records: list[dict] = []
    for h, g, feat in zip(base.history, replayed, feats_hist):
        if h["date"] != g.get("date", "") or h["team_a"] != g["team_a"]:
            raise RuntimeError(
                f"feature/history misalignment at {h['date']}: "
                f"{h['team_a']} vs {g.get('team_a')!r}"
            )
        if h["date"] < _EVAL_START:
            continue
        x_elo = h["pre_a"] - h["pre_b"] + DEFAULT_HOME_ADVANTAGE
        records.append(
            {
                "season": int(h["date"][:4]),
                "row": build_design_row(x_elo, feat),
                "y": 1.0 if h["winner"] == h["team_a"] else 0.0,
                "pre_a": h["pre_a"],
                "pre_b": h["pre_b"],
                "base_prob": expected_win_prob(
                    h["pre_a"], h["pre_b"], DEFAULT_HOME_ADVANTAGE
                ),
            }
        )

    # --- (1) In-sample coefficient estimate (descriptive; significance only) ---
    full = logistic_fit(
        np.array([r["row"] for r in records]), np.array([r["y"] for r in records])
    )
    names = ["intercept", "x_elo", "rest_diff", "b2b_diff", "travel_diff_k", "tz_diff"]
    print(f"In-sample coefficient estimate (descriptive), N={len(records)}:")
    for i, nm in enumerate(names):
        print(
            f"  {nm:<14} coef={full.coef[i]:+.5f}  se={full.stderr[i]:.5f}  "
            f"p={full.pvalues[i]:.4g}"
        )
    er, eb, et, ez = magnitudes_from_fit(full)
    print(
        "\n  Elo-point magnitudes (per unit): "
        f"rest {er:+.1f}, b2b {eb:+.1f}, travel/1000mi {et:+.1f}, tz {ez:+.1f}"
    )
    print("  (In-sample — describes detectability, NOT the ship decision.)")

    # --- (2) Out-of-sample rolling-origin ship gate ---
    seasons = sorted({r["season"] for r in records})
    oos_base: list[float] = []
    oos_adj: list[float] = []
    oos_y: list[float] = []
    used: list[int] = []
    for s in seasons:
        train = [r for r in records if r["season"] < s]
        test = [r for r in records if r["season"] == s]
        if len(train) < _MIN_TRAIN_GAMES or not test:
            continue
        fit = logistic_fit(
            np.array([r["row"] for r in train]), np.array([r["y"] for r in train])
        )
        mags = np.array(magnitudes_from_fit(fit))
        used.append(s)
        for r in test:
            # delta = magnitudes · the same feature diffs the coefs were fit on
            # (row[1:] drops the x_elo column), applied at prediction time.
            delta = float(mags @ r["row"][1:])
            oos_base.append(r["base_prob"])
            oos_adj.append(
                expected_win_prob(
                    r["pre_a"], r["pre_b"], DEFAULT_HOME_ADVANTAGE + delta
                )
            )
            oos_y.append(r["y"])

    if not oos_y:
        print("\nNot enough data for an out-of-sample fold — aborting gate.")
        return

    base_arr, adj_arr, y_arr = np.array(oos_base), np.array(oos_adj), np.array(oos_y)
    print(
        f"\nOut-of-sample rolling gate: test seasons {used} "
        f"(coefs fit only on prior seasons), N={len(y_arr)}"
    )
    print(
        f"  baseline  Brier={brier_score(base_arr, y_arr):.5f}  "
        f"LogLoss={log_loss(base_arr, y_arr):.5f}"
    )
    print(
        f"  adjusted  Brier={brier_score(adj_arr, y_arr):.5f}  "
        f"LogLoss={log_loss(adj_arr, y_arr):.5f}"
    )

    mean_d, lo, hi = bootstrap_brier_delta_ci(base_arr, adj_arr, y_arr, n_boot=2000)
    print("\nBootstrap OOS Brier improvement (baseline - adjusted):")
    print(f"  mean={mean_d:+.6f}  95% CI=[{lo:+.6f}, {hi:+.6f}]")
    verdict = "SHIP" if lo > 0 else "NULL (within noise)"
    print(f"\nVERDICT: {verdict}")
    if lo > 0:
        print("  -> proceed to Plan 2 (integration) with the magnitudes above.")
    else:
        print("  -> document the null; no model change. Stop.")


if __name__ == "__main__":
    main()
