"""End-to-end DARKO-for-WNBA validation driver on REAL 2023-2025 wehoop data.

Wires the whole offline model and prints the honest walk-forward retrodiction
verdict (naive < box_only < full is the hypothesis). This is a driver, not a
library: readable top-to-bottom, no abstractions beyond what the wiring needs.

Run: python -m src.darko.run_validation
"""

import numpy as np
import pandas as pd

from src.darko import box_prior, rapm
from src.darko.ingest import load_pbp, load_player_box
from src.darko.stints import build_stint_rows
from src.darko.validation import walk_forward_retrodiction

# Modeling seasons with full pbp + box (RAPM is data-hungry; 3 seasons is thin).
SEASONS = [2023, 2024, 2025]

# Classic box line as features; plus_minus as the per-game target. These are the
# real wehoop player_box column names (verified against load_player_box(2024)).
STAT_COLS = ["points", "rebounds", "assists", "steals", "blocks", "turnovers"]
TARGET_COL = "plus_minus"

# Heavy ridge: 3 seasons of stints is thin and collinear, so shrink hard toward the
# box prior. This is an honesty choice, not a tuned one — see FINDINGS.
LAM = 2000.0

# Kalman noise. q = process (how fast true skill drifts game-to-game), r = obs
# noise (single-game plus-minus is very noisy). Single-game plus-minus has a large
# spread, so r dominates; these are reasonable a-priori values, not fit to the test.
KALMAN_Q = 0.05
KALMAN_R = 9.0


def _load_played_box(season: int) -> pd.DataFrame:
    """Box rows for players who actually played, with a numeric plus_minus.

    wehoop stores plus_minus as a signed string ('+5', '-4', None for DNP); coerce
    it to float and drop rows missing it or the box stats (DNPs)."""
    df = load_player_box(season)
    df = df[~df["did_not_play"]].copy()
    df["plus_minus"] = pd.to_numeric(
        df["plus_minus"].astype(str).str.replace("+", "", regex=False),
        errors="coerce",
    )
    needed = STAT_COLS + [TARGET_COL, "minutes"]
    df = df.dropna(subset=needed)
    return df


def _build_box_model(box_pool: pd.DataFrame) -> box_prior.BoxModel:
    """Regress the real box line onto plus_minus across pooled 2023-2025."""
    return box_prior.fit(box_pool, STAT_COLS, TARGET_COL)


def _build_stints(seasons) -> pd.DataFrame:
    """Pooled stint-rows across all games in all seasons. build_stint_rows is
    arg-order-safe (derives home/away from pbp internally)."""
    all_rows = []
    for season in seasons:
        try:
            pbp = load_pbp(season)
            box = load_player_box(season)
        except Exception as e:  # one season's download shouldn't sink the run
            print(f"  season {season} load failed: {e}; skipping")
            continue
        games = (
            pbp[["game_id", "home_team_id", "away_team_id"]]
            .dropna()
            .drop_duplicates("game_id")
        )
        for _, gr in games.iterrows():
            gid = int(gr["game_id"])
            home = int(gr["home_team_id"])
            away = int(gr["away_team_id"])
            try:
                rows = build_stint_rows(pbp, box, gid, home, away)
            except Exception as e:  # one ragged game shouldn't sink the pool
                print(f"  stint build failed for game {gid} (season {season}): {e}")
                continue
            if rows is not None and not rows.empty:
                all_rows.append(rows)
    return pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()


def _build_signals(box_pool: pd.DataFrame, model: box_prior.BoxModel) -> pd.DataFrame:
    """Chronological (athlete_id, game_idx, signal) rows. signal = box game_signal
    for that game's box line; game_idx = per-athlete chronological counter."""
    rows = box_pool.sort_values(["athlete_id", "game_date"]).copy()
    rows["signal"] = (
        rows[STAT_COLS].to_numpy(dtype=float) @ model.coef + model.intercept
    )
    rows["game_idx"] = rows.groupby("athlete_id").cumcount()
    return rows[["athlete_id", "game_idx", "signal"]].reset_index(drop=True)


def _build_anchor(impacts, signals: pd.DataFrame, player_prior: pd.Series) -> dict:
    """RAPM .total -> full's Kalman x0, made unit-coherent with the box signal.

    The anchor is a per-100 RAPM impact; the Kalman OBSERVATIONS are per-game
    plus-minus signals. For the anchor to help, it must live on the signal's scale.
    We center BOTH to mean 0 and rescale the (centered) anchor so its spread matches
    the signal's spread, then re-center onto the signal mean. Players with no RAPM
    estimate fall back to the player_prior (box-implied) mapped the same way; if even
    that is missing, anchor defaults to 0 (the signal mean after centering ~ small),
    which the validation layer already handles via anchor.get(pid, 0.0)."""
    sig_mean = float(signals["signal"].mean())
    sig_std = float(signals["signal"].std())

    totals = {imp.player_id: imp.total for imp in impacts}
    # Fall back to box player_prior for players RAPM didn't estimate.
    for pid, val in player_prior.items():
        totals.setdefault(int(pid), float(val))

    vals = np.array(list(totals.values()), dtype=float)
    a_mean = float(vals.mean())
    a_std = float(vals.std()) or 1.0

    anchor = {}
    for pid, v in totals.items():
        z = (v - a_mean) / a_std  # standardize the anchor
        anchor[int(pid)] = sig_mean + z * sig_std  # map onto the signal's scale
    return anchor


def main():
    print("=== DARKO-for-WNBA: end-to-end validation on 2023-2025 ===\n")

    print(f"Loading + pooling player_box for {SEASONS} ...")
    box_pool = pd.concat([_load_played_box(s) for s in SEASONS], ignore_index=True)
    print(f"  pooled played box rows: {len(box_pool)}")

    print("Fitting box-prior model (box line -> plus_minus) ...")
    model = _build_box_model(box_pool)
    print(f"  intercept={model.intercept:.3f}")
    print(f"  coef={dict(zip(STAT_COLS, np.round(model.coef, 4)))}")

    print("Building pooled stint-rows (lineup reconstruction) ...")
    stints = _build_stints(SEASONS)
    print(
        f"  stint-rows: {len(stints)}  (games covered: {stints['game_id'].nunique() if not stints.empty else 0})"
    )

    print("Computing box player_prior + fitting ridge RAPM ...")
    prior = box_prior.player_prior(model, box_pool)
    impacts = rapm.fit_rapm(stints, prior, prior, lam=LAM)
    print(f"  RAPM estimated {len(impacts)} players (lam={LAM})")

    print("Building chronological signals ...")
    signals = _build_signals(box_pool, model)
    print(f"  signal rows: {len(signals)}  athletes: {signals['athlete_id'].nunique()}")

    print("Building unit-coherent anchor (RAPM .total -> signal scale) ...")
    anchor = _build_anchor(impacts, signals, prior)
    print(f"  anchor players: {len(anchor)}")

    # Aging: player_box has NO age / birth-date column (verified). Aging is not
    # wired on real data; the validation layer supports an empty drift cleanly.
    drift = {}
    print("Aging: NOT wired (no age column in wehoop player_box).\n")

    print("Running walk-forward retrodiction ...")
    res = walk_forward_retrodiction(
        signals, q=KALMAN_Q, r=KALMAN_R, anchor=anchor, drift=drift
    )

    mae = res["mae"]
    cov = res["coverage_80"]
    print("\n--- Retrodiction MAE (out-of-sample, walk-forward) ---")
    print(f"  naive           : {mae['naive']:.4f}")
    print(f"  kalman          : {mae['kalman']:.4f}")
    print(f"  kalman_anchored : {mae['kalman_anchored']:.4f}")
    print("\n--- 80% interval coverage ---")
    print(f"  kalman          : {cov['kalman']:.3f}")
    print(f"  kalman_anchored : {cov['kalman_anchored']:.3f}")

    box_vs_naive = mae["naive"] - mae["kalman"]
    full_vs_box = mae["kalman"] - mae["kalman_anchored"]
    print("\n--- VERDICT ---")
    print(
        f"  kalman beats naive? {box_vs_naive > 0}  "
        f"(delta {box_vs_naive:+.4f}, lower MAE is better)"
    )
    print(
        f"  kalman_anchored beats kalman?  {full_vs_box > 0}  "
        f"(delta {full_vs_box:+.4f}, lower MAE is better)"
    )


if __name__ == "__main__":
    main()
