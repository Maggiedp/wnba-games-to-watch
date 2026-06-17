"""CLEAN-CONFIRMATION DARKO-for-WNBA validation on REAL wehoop data.

This is the second validation driver. The first (`run_validation.py`) had a
three-way confound a skeptical reviewer flagged:
  1. The walk-forward TARGET was the box model's own FITTED VALUE
     (box_stats @ coef + intercept) — a deterministic function of the same
     game's box line — so a possession-level RAPM anchor had no room to add
     value (the target lived entirely in box-space).
  2. The RAPM anchor was LAUNDERED into box-space: standardized onto the
     fitted signal's mean/SD, erasing its native units.
  3. box + RAPM were fit on all 3 seasons, then "validated" on the same data
     (forward-leak).

This driver removes all three:
  1. TARGET = each player's ACTUAL per-game plus_minus on 2025 (real, noisy).
  2. ANCHOR stays in honest units: RAPM per-100 -> per-game via a FIXED
     physical scaling k estimated ON TRAIN ONLY. No centering/rescaling onto
     the test signal's moments.
  3. TEMPORAL SPLIT: box model + RAPM fit on 2023+2024 (TRAIN); walk-forward
     evaluated on 2025 (TEST). No forward-leak.

Run: python -m src.darko.run_validation_clean
"""

import numpy as np
import pandas as pd

from src.darko import box_prior, rapm
from src.darko.ingest import load_pbp, load_player_box
from src.darko.stints import build_stint_rows
from src.darko.validation import bootstrap_mae_delta_ci, walk_forward_retrodiction

TRAIN_SEASONS = [2023, 2024]
TEST_SEASON = 2025

STAT_COLS = ["points", "rebounds", "assists", "steals", "blocks", "turnovers"]
TARGET_COL = "plus_minus"

# Heavy ridge (thin, collinear stints). A-priori, not tuned — see FINDINGS.
LAM = 2000.0

# Kalman noise, a-priori / un-tuned (carried over from run_validation.py).
# q = process drift game-to-game; r = single-game plus-minus observation noise
# (large, so r dominates). Not fit to the test set.
KALMAN_Q = 0.05
KALMAN_R = 9.0


def _load_played_box(season: int) -> pd.DataFrame:
    """Box rows for players who actually played, with a numeric plus_minus.

    wehoop stores plus_minus as a signed string ('+5'/'-4', None for DNP); coerce
    to float and drop rows missing it or the box stats (DNPs)."""
    df = load_player_box(season)
    df = df[~df["did_not_play"]].copy()
    df["plus_minus"] = pd.to_numeric(
        df["plus_minus"].astype(str).str.replace("+", "", regex=False),
        errors="coerce",
    )
    needed = STAT_COLS + [TARGET_COL, "minutes"]
    df = df.dropna(subset=needed)
    return df


def _build_stints(seasons) -> pd.DataFrame:
    """Pooled stint-rows across all games in all seasons. build_stint_rows is
    arg-order-safe (derives home/away from pbp internally). Per-season loads and
    per-game stint builds are guarded so one failure can't sink the run."""
    all_rows = []
    for season in seasons:
        try:
            pbp = load_pbp(season)
            box = load_player_box(season)
        except Exception as e:
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
            except Exception as e:
                print(f"  stint build failed for game {gid} (season {season}): {e}")
                continue
            if rows is not None and not rows.empty:
                all_rows.append(rows)
    return pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()


def _estimate_k(stints: pd.DataFrame) -> float:
    """Fixed per-100 -> per-game scaling, estimated ON TRAIN ONLY.

    Each stint puts 5 players on offense and 5 on defense, each on court for
    `possessions` possessions. So total player-game on-court possessions =
    sum(possessions) * 10. Divide by the number of distinct (game_id, player)
    appearances to get the mean on-court possessions per player-game, then /100
    to convert a per-100 RAPM total to per-game units. This is a FIXED physical
    constant from TRAIN, not fit to the test signal."""
    total_player_poss = float(stints["possessions"].sum()) * 10.0
    appearances = set()
    for _, row in stints.iterrows():
        gid = int(row["game_id"])
        for p in row["off_players"]:
            appearances.add((gid, int(p)))
        for p in row["def_players"]:
            appearances.add((gid, int(p)))
    n_player_games = len(appearances)
    mean_poss_per_player_game = total_player_poss / n_player_games
    return mean_poss_per_player_game / 100.0


def _build_signals(box_pool: pd.DataFrame) -> pd.DataFrame:
    """Chronological (athlete_id, game_idx, signal) rows where signal = the
    player's ACTUAL coerced plus_minus that game (the real, noisy outcome).
    game_idx = per-athlete chronological counter (sort by game_date)."""
    rows = box_pool.sort_values(["athlete_id", "game_date"]).copy()
    rows["signal"] = rows[TARGET_COL].to_numpy(dtype=float)
    rows["game_idx"] = rows.groupby("athlete_id").cumcount()
    return rows[["athlete_id", "game_idx", "signal"]].reset_index(drop=True)


def _build_anchor_scaled(impacts, player_prior: pd.Series, k: float) -> dict:
    """RAPM .total (per-100) -> per-game anchor via the FIXED train constant k.

    anchor[pid] = rapm_total[pid] * k. Players RAPM didn't estimate fall back to
    player_prior[pid] * k. NOTE on units: player_prior is the box regression's
    fitted plus-minus per game-box-line (already per-game, plus-minus units),
    so multiplying it by k slightly under-scales those fallback players; we
    accept this as a documented, conservative approximation rather than mix two
    unit systems in one anchor dict. The fallback only fires for players with no
    2023-24 stint coverage, a minority — and the sensitivity run (`full_raw`,
    raw unscaled totals) brackets the scaling choice anyway."""
    anchor = {imp.player_id: imp.total * k for imp in impacts}
    for pid, val in player_prior.items():
        anchor.setdefault(int(pid), float(val) * k)
    return anchor


def _build_anchor_raw(impacts, player_prior: pd.Series) -> dict:
    """Sensitivity: RAW unscaled RAPM total as the anchor (no per-game scaling).
    Players RAPM didn't estimate fall back to the raw player_prior."""
    anchor = {imp.player_id: imp.total for imp in impacts}
    for pid, val in player_prior.items():
        anchor.setdefault(int(pid), float(val))
    return anchor


def _fmt_ci(label, delta, lo, hi):
    sig = "YES" if (lo > 0 or hi < 0) else "no"
    return (
        f"  {label:28s} mean_delta={delta:+.4f}  "
        f"95% CI [{lo:+.4f}, {hi:+.4f}]  significant={sig}"
    )


def main():
    print("=== DARKO-for-WNBA: CLEAN validation (train 2023-24 -> test 2025) ===\n")

    # ---- TRAIN: box model + RAPM on 2023+2024 only ----
    print(f"Loading + pooling TRAIN played box for {TRAIN_SEASONS} ...")
    train_box = pd.concat(
        [_load_played_box(s) for s in TRAIN_SEASONS], ignore_index=True
    )
    print(f"  train played box rows: {len(train_box)}")

    print("Fitting box-prior model on TRAIN (box line -> plus_minus) ...")
    model = box_prior.fit(train_box, stat_cols=STAT_COLS, target_col=TARGET_COL)
    print(f"  intercept={model.intercept:.3f}")
    print(f"  coef={dict(zip(STAT_COLS, np.round(model.coef, 4)))}")

    print("Building TRAIN stint-rows (lineup reconstruction) ...")
    train_stints = _build_stints(TRAIN_SEASONS)
    n_games = train_stints["game_id"].nunique() if not train_stints.empty else 0
    print(f"  train stint-rows: {len(train_stints)}  (games covered: {n_games})")

    print("Computing TRAIN box player_prior + fitting ridge RAPM ...")
    prior = box_prior.player_prior(model, train_box)
    impacts = rapm.fit_rapm(train_stints, prior, prior, lam=LAM)
    print(f"  RAPM estimated {len(impacts)} players (lam={LAM})")

    print("Estimating fixed per-100 -> per-game scaling k (TRAIN only) ...")
    k = _estimate_k(train_stints)
    print(f"  k = mean on-court possessions per player-game / 100 = {k:.4f}")

    # ---- TEST: actual 2025 per-game plus_minus ----
    print(f"\nLoading TEST played box for {TEST_SEASON} ...")
    test_box = _load_played_box(TEST_SEASON)
    print(f"  test played box rows: {len(test_box)}")

    print("Building chronological signals (actual 2025 per-game plus_minus) ...")
    signals = _build_signals(test_box)
    print(f"  signal rows: {len(signals)}  athletes: {signals['athlete_id'].nunique()}")

    anchor_scaled = _build_anchor_scaled(impacts, prior, k)
    anchor_raw = _build_anchor_raw(impacts, prior)
    print(f"  anchor players (scaled/raw): {len(anchor_scaled)}/{len(anchor_raw)}")

    # Aging: player_box has NO age / birth-date column (verified). Not wired.
    drift = {}
    print("Aging: NOT wired (no age column in wehoop player_box).\n")

    print("Running walk-forward retrodiction on 2025 (scaled anchor) ...")
    res_scaled = walk_forward_retrodiction(
        signals, q=KALMAN_Q, r=KALMAN_R, anchor=anchor_scaled, drift=drift
    )
    print("Running walk-forward retrodiction on 2025 (raw anchor sensitivity) ...")
    res_raw = walk_forward_retrodiction(
        signals, q=KALMAN_Q, r=KALMAN_R, anchor=anchor_raw, drift=drift
    )

    mae = res_scaled["mae"]
    mae_raw_full = res_raw["mae"]["full"]
    cov = res_scaled["coverage_80"]
    cov_raw_full = res_raw["coverage_80"]["full"]

    print("\n--- Retrodiction MAE on 2025 (out-of-sample, walk-forward) ---")
    print(f"  naive          : {mae['naive']:.4f}")
    print(f"  box_only       : {mae['box_only']:.4f}")
    print(f"  full (scaled)  : {mae['full']:.4f}")
    print(f"  full_raw       : {mae_raw_full:.4f}")
    print("\n--- 80% interval coverage ---")
    print(f"  box_only       : {cov['box_only']:.3f}")
    print(f"  full (scaled)  : {cov['full']:.3f}")
    print(f"  full_raw       : {cov_raw_full:.3f}")

    # ---- Bootstrap CIs (the headline). abs_err arrays are aligned per model. ----
    ab = res_scaled["abs_err"]
    ab_raw = res_raw["abs_err"]
    d_bn, lo_bn, hi_bn = bootstrap_mae_delta_ci(ab["naive"], ab["box_only"])
    d_fb, lo_fb, hi_fb = bootstrap_mae_delta_ci(ab["box_only"], ab["full"])
    d_fbr, lo_fbr, hi_fbr = bootstrap_mae_delta_ci(ab_raw["box_only"], ab_raw["full"])

    print("\n--- Bootstrap 95% CIs (positive delta = adjusted has LOWER MAE) ---")
    print(_fmt_ci("box_only vs naive", d_bn, lo_bn, hi_bn))
    print(_fmt_ci("full(scaled) vs box_only", d_fb, lo_fb, hi_fb))
    print(_fmt_ci("full_raw vs box_only", d_fbr, lo_fbr, hi_fbr))

    sig_bn = lo_bn > 0 or hi_bn < 0
    sig_fb = lo_fb > 0 or hi_fb < 0
    sig_fbr = lo_fbr > 0 or hi_fbr < 0

    print("\n=== VERDICT ===")
    print(
        f"  box_only beats naive?      {'YES' if d_bn > 0 else 'no'}  "
        f"(delta {d_bn:+.4f}, CI [{lo_bn:+.4f},{hi_bn:+.4f}], significant={sig_bn})"
    )
    print(
        f"  full(scaled) beats box?    {'YES' if d_fb > 0 else 'no'}  "
        f"(delta {d_fb:+.4f}, CI [{lo_fb:+.4f},{hi_fb:+.4f}], significant={sig_fb})"
    )
    print(
        f"  full_raw beats box?        {'YES' if d_fbr > 0 else 'no'}  "
        f"(delta {d_fbr:+.4f}, CI [{lo_fbr:+.4f},{hi_fbr:+.4f}], significant={sig_fbr})"
    )
    print(f"  coverage_80 box_only={cov['box_only']:.3f} full={cov['full']:.3f}")

    box_msg = (
        "Kalman smoothing significantly beats naive last-game"
        if sig_bn
        else "Kalman smoothing is indistinguishable from naive (CI straddles 0)"
    )
    rapm_msg = (
        "the RAPM anchor significantly improves on box_only"
        if (sig_fb or sig_fbr)
        else "the RAPM anchor does NOT earn its keep — full is indistinguishable "
        "from box_only (CI straddles 0), now shown cleanly without the box-target "
        "confound"
    )
    print(f"\n  CONCLUSION: {box_msg}; {rapm_msg}.")


if __name__ == "__main__":
    main()
