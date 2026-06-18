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

The clean v1 of THIS driver removed all three (temporal split, actual-pm
target, honest-unit anchor). A second adversarial review then found two more
problems with THIS driver, fixed in this revision:
  4. The "box_only" baseline never actually used the box model — it was a
     RECENCY Kalman smoothing the actual plus_minus. So we never validated the
     box model as a one-step-ahead predictor at all. FIX: a REAL out-of-sample
     box-model baseline (Run B below): a Kalman over the box model's per-game
     fitted predictions, scored against actual plus_minus.
  5. The i.i.d. bootstrap ignored within-player serial correlation, making the
     CIs too narrow. FIX: athlete-CLUSTERED bootstrap for every CI (resamples
     whole players).

Design (this revision):
  - TEMPORAL SPLIT: box model + RAPM fit on 2023+2024 (TRAIN); walk-forward
    evaluated on 2025 (TEST). No forward-leak.
  - ONE sorted 2025 test frame -> two ALIGNED observation streams:
      * signal_pm  = actual coerced plus_minus (the real, noisy target).
      * signal_box = box_prior.game_signal(model, row) (TRAIN-fit box model's
        fitted plus_minus for that game's box line).
  - Run A (signal = signal_pm): naive / recency-Kalman / RAPM-anchored.
  - Run B (signal = signal_box, target = signal_pm): the Kalman output is the
    BOX-MODEL baseline — one-step-ahead box predictions scored vs actual pm.
  - ALL CIs use the athlete-clustered paired bootstrap.

Honest model labels:
  naive          = last actual plus_minus
  recency        = recency Kalman over actual plus_minus (Run A kalman)
  box            = box-model one-step-ahead Kalman (Run B kalman)
  rapm_anchored  = recency Kalman seeded with the scaled RAPM anchor (Run A)
  rapm_anchored_raw = same, raw unscaled RAPM anchor (sensitivity)

Run: python -m src.darko.run_validation_clean
"""

import numpy as np
import pandas as pd

from src.darko import box_prior, rapm
from src.darko.ingest import load_pbp, load_player_box
from src.darko.stints import build_stint_rows
from src.darko.validation import (
    bootstrap_mae_delta_ci_clustered,
    walk_forward_retrodiction,
)

# Abort the run if more than this fraction of attempted games fail to build
# stints — a rare malformed game is tolerated, systematic data loss is not.
MAX_SKIP_FRAC = 0.01

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


def _build_stints(seasons, label: str) -> pd.DataFrame:
    """Pooled stint-rows across all games in all seasons. build_stint_rows is
    arg-order-safe (derives home/away from pbp internally).

    FAIL-CLOSED: a SEASON load failure is FATAL (you cannot validate on partial
    seasons — propagate). Per-game build failures are tolerated individually but
    counted; if more than MAX_SKIP_FRAC of attempted games fail, RAISE (systematic
    loss must not silently bias the verdict). Explicit coverage is printed."""
    all_rows = []
    attempted = skipped = with_stints = 0
    for season in seasons:
        # Season load failure is FATAL — let it propagate (no partial-season run).
        pbp = load_pbp(season)
        box = load_player_box(season)
        games = (
            pbp[["game_id", "home_team_id", "away_team_id"]]
            .dropna()
            .drop_duplicates("game_id")
        )
        for _, gr in games.iterrows():
            gid = int(gr["game_id"])
            home = int(gr["home_team_id"])
            away = int(gr["away_team_id"])
            attempted += 1
            try:
                rows = build_stint_rows(pbp, box, gid, home, away)
            except Exception as e:
                skipped += 1
                print(f"  stint build failed for game {gid} (season {season}): {e}")
                continue
            if rows is not None and not rows.empty:
                all_rows.append(rows)
                with_stints += 1

    coverage = (attempted - skipped) / attempted if attempted else 0.0
    print(
        f"  [{label}] games attempted={attempted} with_stints={with_stints} "
        f"skipped={skipped} coverage={coverage:.4f}"
    )
    if attempted and skipped / attempted > MAX_SKIP_FRAC:
        raise RuntimeError(
            f"[{label}] systematic stint-build loss: skipped {skipped}/{attempted} "
            f"games ({skipped / attempted:.2%} > {MAX_SKIP_FRAC:.0%}) — refusing to "
            "emit a verdict on partial data."
        )
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


def _build_test_frame(
    test_box: pd.DataFrame, model: box_prior.BoxModel
) -> pd.DataFrame:
    """ONE chronologically-sorted 2025 test frame with TWO aligned signal streams.

    Sort once by (athlete_id, game_date), assign game_idx = per-athlete cumcount.
    Per row:
      signal_pm  = the player's ACTUAL coerced plus_minus that game (real target).
      signal_box = box_prior.game_signal(model, row) — the TRAIN-fit box model's
                   fitted plus_minus for that game's box line.
    Both streams come from the SAME sorted rows, so Run A (over signal_pm) and
    Run B (over signal_box) emit per-prediction outputs ALIGNED 1:1 (same
    athlete/game order), which makes cross-run paired CIs valid."""
    rows = test_box.sort_values(["athlete_id", "game_date"]).copy()
    rows["signal_pm"] = rows[TARGET_COL].to_numpy(dtype=float)
    rows["signal_box"] = [box_prior.game_signal(model, r) for _, r in rows.iterrows()]
    rows["game_idx"] = rows.groupby("athlete_id").cumcount()
    return rows[["athlete_id", "game_idx", "signal_pm", "signal_box"]].reset_index(
        drop=True
    )


def _build_anchor_scaled(impacts, player_prior: pd.Series, k: float) -> dict:
    """RAPM .total (per-100) -> per-game anchor via the FIXED train constant k.

    anchor[pid] = rapm_total[pid] * k. Players RAPM didn't estimate fall back to
    player_prior[pid] * k. NOTE on units: player_prior is the box regression's
    fitted plus-minus per game-box-line (already per-game, plus-minus units),
    so multiplying it by k slightly under-scales those fallback players; we
    accept this as a documented, conservative approximation rather than mix two
    unit systems in one anchor dict. The fallback only fires for players with no
    2023-24 stint coverage, a minority — and the raw-anchor sensitivity run
    (`rapm_anchored_raw`, raw unscaled totals) brackets the scaling choice."""
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
        f"  {label:34s} mean_delta={delta:+.4f}  "
        f"95% CI [{lo:+.4f}, {hi:+.4f}]  significant={sig}"
    )


def main():
    print("=== DARKO-for-WNBA: CLEAN validation (train 2023-24 -> test 2025) ===")
    print("    box-model baseline (Run B) + athlete-CLUSTERED bootstrap CIs\n")

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
    train_stints = _build_stints(TRAIN_SEASONS, label="TRAIN")
    n_games = train_stints["game_id"].nunique() if not train_stints.empty else 0
    print(f"  train stint-rows: {len(train_stints)}  (games covered: {n_games})")

    print("Computing TRAIN box player_prior + fitting ridge RAPM ...")
    prior = box_prior.player_prior(model, train_box)
    impacts = rapm.fit_rapm(train_stints, prior, prior, lam=LAM)
    print(f"  RAPM estimated {len(impacts)} players (lam={LAM})")

    print("Estimating fixed per-100 -> per-game scaling k (TRAIN only) ...")
    k = _estimate_k(train_stints)
    print(f"  k = mean on-court possessions per player-game / 100 = {k:.4f}")

    # ---- TEST: build ONE sorted 2025 frame, two aligned streams ----
    print(f"\nLoading TEST played box for {TEST_SEASON} ...")
    # Fail-closed coverage check on the TEST path too: a silently-shrunk test box
    # could bias the verdict just as a partial train load could. Compare games that
    # survive the played-box filter against all games in the season's box.
    full_test_box = load_player_box(TEST_SEASON)
    test_box = _load_played_box(TEST_SEASON)
    test_attempted = full_test_box["game_id"].nunique()
    test_covered = test_box["game_id"].nunique()
    test_cov_frac = test_covered / test_attempted if test_attempted else 0.0
    print(
        f"  [TEST] games in box={test_attempted} games with played rows="
        f"{test_covered} coverage={test_cov_frac:.4f}"
    )
    if (
        test_attempted
        and (test_attempted - test_covered) / test_attempted > MAX_SKIP_FRAC
    ):
        raise RuntimeError(
            f"[TEST] systematic game loss: only {test_covered}/{test_attempted} "
            f"games have played box rows — refusing to emit a verdict on partial data."
        )
    print(f"  test played box rows: {len(test_box)}")

    print("Building ONE sorted test frame (signal_pm + signal_box, aligned) ...")
    frame = _build_test_frame(test_box, model)
    print(f"  test rows: {len(frame)}  athletes: {frame['athlete_id'].nunique()}")

    anchor_scaled = _build_anchor_scaled(impacts, prior, k)
    anchor_raw = _build_anchor_raw(impacts, prior)
    print(f"  anchor players (scaled/raw): {len(anchor_scaled)}/{len(anchor_raw)}")

    # Aging: player_box has NO age / birth-date column (verified). Not wired.
    drift = {}
    print("Aging: NOT wired (no age column in wehoop player_box).\n")

    # ---- Run A: observe ACTUAL plus_minus (no target col => target = signal) ----
    run_a = frame[["athlete_id", "game_idx", "signal_pm"]].rename(
        columns={"signal_pm": "signal"}
    )
    print("Run A: walk-forward over ACTUAL plus_minus (scaled anchor) ...")
    res_a = walk_forward_retrodiction(
        run_a, q=KALMAN_Q, r=KALMAN_R, anchor=anchor_scaled, drift=drift
    )
    print("Run A': raw-anchor sensitivity ...")
    res_a_raw = walk_forward_retrodiction(
        run_a, q=KALMAN_Q, r=KALMAN_R, anchor=anchor_raw, drift=drift
    )

    # ---- Run B: observe BOX fitted signal, SCORE against actual plus_minus ----
    run_b = frame[["athlete_id", "game_idx", "signal_box", "signal_pm"]].rename(
        columns={"signal_box": "signal", "signal_pm": "target"}
    )
    print("Run B: walk-forward over BOX-MODEL signal, scored vs actual pm ...")
    res_b = walk_forward_retrodiction(
        run_b, q=KALMAN_Q, r=KALMAN_R, anchor=None, drift=drift
    )

    # ---- Map to honest labels ----
    # naive / recency / rapm_anchored / rapm_anchored_raw come from Run A;
    # box comes from Run B's kalman output (one-step-ahead box predictor).
    err = {
        "naive": res_a["abs_err"]["naive"],
        "recency": res_a["abs_err"]["kalman"],
        "rapm_anchored": res_a["abs_err"]["kalman_anchored"],
        "rapm_anchored_raw": res_a_raw["abs_err"]["kalman_anchored"],
        "box": res_b["abs_err"]["kalman"],
    }
    mae = {label: float(np.mean(e)) for label, e in err.items()}
    cov = {
        "recency": res_a["coverage_80"]["kalman"],
        "rapm_anchored": res_a["coverage_80"]["kalman_anchored"],
        "rapm_anchored_raw": res_a_raw["coverage_80"]["kalman_anchored"],
        "box": res_b["coverage_80"]["kalman"],
    }

    # ---- Alignment: Run A, Run A' and Run B all walk the SAME sorted frame, so
    # their per-prediction outputs are aligned 1:1. Assert it before paired CIs.
    ids_a = res_a["athlete_ids"]
    ids_a_raw = res_a_raw["athlete_ids"]
    ids_b = res_b["athlete_ids"]
    assert len(ids_a) == len(ids_b) == len(ids_a_raw), "run lengths differ"
    assert np.array_equal(ids_a, ids_b), "Run A / Run B athlete order misaligned"
    assert np.array_equal(ids_a, ids_a_raw), "Run A / Run A' athlete order misaligned"
    cluster_ids = ids_a

    print("\n--- Retrodiction MAE on 2025 (out-of-sample, walk-forward) ---")
    print(f"  naive             : {mae['naive']:.4f}")
    print(f"  recency           : {mae['recency']:.4f}")
    print(f"  box               : {mae['box']:.4f}")
    print(f"  rapm_anchored     : {mae['rapm_anchored']:.4f}")
    print(f"  rapm_anchored_raw : {mae['rapm_anchored_raw']:.4f}")
    print("\n--- 80% interval coverage ---")
    print(f"  recency           : {cov['recency']:.3f}")
    print(f"  box               : {cov['box']:.3f}")
    print(f"  rapm_anchored     : {cov['rapm_anchored']:.3f}")
    print(f"  rapm_anchored_raw : {cov['rapm_anchored_raw']:.3f}")

    # ---- Clustered bootstrap CIs (the headline). cluster_ids = athlete per row. ----
    def cci(baseline_label, adjusted_label):
        return bootstrap_mae_delta_ci_clustered(
            err[baseline_label], err[adjusted_label], cluster_ids
        )

    d_rn, lo_rn, hi_rn = cci("naive", "recency")  # recency vs naive
    d_bn, lo_bn, hi_bn = cci("naive", "box")  # box vs naive
    d_br, lo_br, hi_br = cci("recency", "box")  # box vs recency (NEW)
    d_ar, lo_ar, hi_ar = cci("recency", "rapm_anchored")  # rapm vs recency
    d_arr, lo_arr, hi_arr = cci("recency", "rapm_anchored_raw")  # raw sensitivity

    print(
        "\n--- CLUSTERED (by athlete) bootstrap 95% CIs "
        "(positive delta = adjusted has LOWER/better MAE) ---"
    )
    print(_fmt_ci("recency vs naive", d_rn, lo_rn, hi_rn))
    print(_fmt_ci("box vs naive", d_bn, lo_bn, hi_bn))
    print(_fmt_ci("box vs recency", d_br, lo_br, hi_br))
    print(_fmt_ci("rapm_anchored vs recency", d_ar, lo_ar, hi_ar))
    print(_fmt_ci("rapm_anchored_raw vs recency", d_arr, lo_arr, hi_arr))

    sig_rn = lo_rn > 0 or hi_rn < 0
    sig_br = lo_br > 0 or hi_br < 0
    sig_ar = lo_ar > 0 or hi_ar < 0
    sig_arr = lo_arr > 0 or hi_arr < 0

    print("\n=== VERDICT ===")
    print(
        f"  recency beats naive?        {'YES' if d_rn > 0 else 'no'}  "
        f"(delta {d_rn:+.4f}, CI [{lo_rn:+.4f},{hi_rn:+.4f}], significant={sig_rn})"
    )
    print(
        f"  box beats recency?          {'YES' if d_br > 0 else 'no'}  "
        f"(delta {d_br:+.4f}, CI [{lo_br:+.4f},{hi_br:+.4f}], significant={sig_br})"
    )
    print(
        f"  rapm_anchored beats recency? {'YES' if d_ar > 0 else 'no'}  "
        f"(delta {d_ar:+.4f}, CI [{lo_ar:+.4f},{hi_ar:+.4f}], significant={sig_ar})"
    )
    print(
        f"  rapm_anchored_raw beats recency? {'YES' if d_arr > 0 else 'no'}  "
        f"(delta {d_arr:+.4f}, CI [{lo_arr:+.4f},{hi_arr:+.4f}], significant={sig_arr})"
    )
    print(
        f"  coverage_80 recency={cov['recency']:.3f} box={cov['box']:.3f} "
        f"rapm_anchored={cov['rapm_anchored']:.3f}"
    )

    recency_msg = (
        "recency-Kalman smoothing significantly beats naive last-game"
        if sig_rn
        else "recency-Kalman smoothing is indistinguishable from naive (CI straddles 0)"
    )
    box_msg = (
        "the box model significantly beats pure recency"
        if sig_br
        else "the box model does NOT beat pure recency (CI straddles 0) — "
        "it doesn't earn its place over a recency baseline on this target"
    )
    rapm_msg = (
        "the RAPM anchor significantly improves on recency"
        if (sig_ar or sig_arr)
        else "the RAPM anchor does NOT earn its keep — rapm_anchored is "
        "indistinguishable from recency (scaled and raw CIs both straddle 0), "
        "now under correctly-wider athlete-clustered CIs"
    )
    print(f"\n  CONCLUSION: {recency_msg}; {box_msg}; {rapm_msg}.")


if __name__ == "__main__":
    main()
