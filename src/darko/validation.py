"""Walk-forward retrodiction across games in date order. One-step-ahead: each
prediction uses only prior games. Layered baselines so each model component must
earn its place out of sample (naive < box_only < full is the hypothesis)."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
from src.darko.kalman import KalmanFilter


def walk_forward_retrodiction(
    signals: pd.DataFrame,
    q: float,
    r: float,
    anchor: dict | None = None,
    drift: dict | None = None,
) -> dict:
    """signals: rows of (athlete_id, game_idx, signal) in chronological order.
    anchor[athlete_id] seeds full's x0 (RAPM); drift[(athlete_id, game_idx)] is the
    aging drift for full. Returns MAE per model + 80% interval coverage."""
    anchor = anchor or {}
    drift = drift or {}
    models = ["naive", "box_only", "full"]
    abs_err = {m: [] for m in models}
    covered = {m: 0 for m in ("box_only", "full")}
    counted = {m: 0 for m in ("box_only", "full")}
    z80 = NormalDist().inv_cdf(0.5 + 0.80 / 2)

    for pid, grp in signals.sort_values("game_idx").groupby("athlete_id"):
        kf_box = KalmanFilter(x0=0.0, p0=9.0, q=q, r=r)
        kf_full = KalmanFilter(x0=anchor.get(pid, 0.0), p0=9.0, q=q, r=r)
        last = None
        for _, row in grp.iterrows():
            z = float(row["signal"])
            # predictions BEFORE seeing z
            pred_naive = last if last is not None else 0.0
            pred_box = kf_box.predict(drift=0.0)
            pred_full = kf_full.predict(drift=drift.get((pid, row["game_idx"]), 0.0))
            preds = {"naive": pred_naive, "box_only": pred_box, "full": pred_full}
            for m, p in preds.items():
                abs_err[m].append(abs(p - z))
            # one-step-ahead PREDICTIVE interval for the OBSERVATION z (not the
            # state): observation variance is the post-predict state variance p
            # plus the observation noise r. kf.interval() returns the state-only
            # interval (var p) which structurally under-covers the noisy signal.
            for m, kf in (("box_only", kf_box), ("full", kf_full)):
                half = z80 * math.sqrt(kf.p + kf.r)
                counted[m] += 1
                if kf.x - half <= z <= kf.x + half:
                    covered[m] += 1
            kf_box.update(z)
            kf_full.update(z)
            last = z

    return {
        "mae": {m: float(np.mean(abs_err[m])) for m in models},
        "coverage_80": {m: covered[m] / counted[m] for m in ("box_only", "full")},
        # per-prediction absolute errors, ALIGNED across models (element i is the
        # same prediction event), for paired bootstrap of MAE deltas.
        "abs_err": {m: np.asarray(abs_err[m], dtype=float) for m in models},
    }


def bootstrap_mae_delta_ci(err_baseline, err_adjusted, n_boot=1000, seed=0, ci=0.95):
    """Paired bootstrap CI of the MAE improvement (baseline - adjusted).

    err_* are per-prediction absolute-error arrays, ALIGNED (element i is the same
    prediction event for both). Resamples indices with replacement (paired), so the
    same draws score both models. Positive = adjusted has the LOWER (better) MAE.
    Returns (mean_delta, ci_low, ci_high). Mirrors
    scripts/_calibration.bootstrap_brier_delta_ci."""
    err_baseline = np.asarray(err_baseline, dtype=float)
    err_adjusted = np.asarray(err_adjusted, dtype=float)
    n = len(err_baseline)
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        deltas[i] = err_baseline[idx].mean() - err_adjusted[idx].mean()
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(deltas, [alpha, 1.0 - alpha])
    return float(deltas.mean()), float(lo), float(hi)
