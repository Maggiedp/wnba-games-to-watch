"""Walk-forward retrodiction across games in date order. One-step-ahead: each
prediction uses only prior games. Layered baselines so each model component must
earn its place out of sample (naive < kalman < kalman_anchored is the hypothesis).

Model keys name the MECHANISM, not the observation fed in:
  - naive           : predict the last observed signal
  - kalman          : recency Kalman, no anchor (x0=0)
  - kalman_anchored : same Kalman seeded from an anchor (RAPM) x0
The Kalman UPDATES on `signal` (the observation); predictions are SCORED against
`target` (defaults to `signal` when no target column is supplied)."""

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
    """signals: rows of (athlete_id, game_idx, signal[, target]) in chronological
    order. The Kalman observes `signal`; predictions are scored against `target`
    (defaults to `signal` if the column is absent). anchor[athlete_id] seeds
    kalman_anchored's x0 (RAPM); drift[(athlete_id, game_idx)] is the aging drift
    for kalman_anchored. Returns MAE per model + 80% interval coverage + aligned
    per-prediction abs_err arrays + aligned athlete_ids (for cluster bootstrap)."""
    anchor = anchor or {}
    drift = drift or {}
    models = ["naive", "kalman", "kalman_anchored"]
    abs_err = {m: [] for m in models}
    covered = {m: 0 for m in ("kalman", "kalman_anchored")}
    counted = {m: 0 for m in ("kalman", "kalman_anchored")}
    athlete_ids = []
    z80 = NormalDist().inv_cdf(0.5 + 0.80 / 2)

    for pid, grp in signals.sort_values("game_idx").groupby("athlete_id"):
        kf = KalmanFilter(x0=0.0, p0=9.0, q=q, r=r)
        kf_anchored = KalmanFilter(x0=anchor.get(pid, 0.0), p0=9.0, q=q, r=r)
        last = None
        for _, row in grp.iterrows():
            obs = float(row["signal"])
            tgt = float(row["target"]) if "target" in row else obs
            # predictions BEFORE seeing obs
            pred_naive = last if last is not None else 0.0
            pred_kalman = kf.predict(drift=0.0)
            pred_anchored = kf_anchored.predict(
                drift=drift.get((pid, row["game_idx"]), 0.0)
            )
            preds = {
                "naive": pred_naive,
                "kalman": pred_kalman,
                "kalman_anchored": pred_anchored,
            }
            for m, p in preds.items():
                abs_err[m].append(abs(p - tgt))
            athlete_ids.append(pid)
            # one-step-ahead PREDICTIVE interval for the OBSERVATION (not the
            # state): observation variance is the post-predict state variance p
            # plus the observation noise r. kf.interval() returns the state-only
            # interval (var p) which structurally under-covers the noisy signal.
            # Coverage is checked against the scoring target.
            for m, kfm in (("kalman", kf), ("kalman_anchored", kf_anchored)):
                half = z80 * math.sqrt(kfm.p + kfm.r)
                counted[m] += 1
                if kfm.x - half <= tgt <= kfm.x + half:
                    covered[m] += 1
            kf.update(obs)
            kf_anchored.update(obs)
            last = obs

    return {
        "mae": {m: float(np.mean(abs_err[m])) for m in models},
        "coverage_80": {
            m: covered[m] / counted[m] for m in ("kalman", "kalman_anchored")
        },
        # per-prediction absolute errors, ALIGNED across models (element i is the
        # same prediction event), for paired bootstrap of MAE deltas.
        "abs_err": {m: np.asarray(abs_err[m], dtype=float) for m in models},
        # athlete_id per prediction event, ALIGNED with the abs_err arrays, so
        # callers can cluster-bootstrap by player.
        "athlete_ids": np.asarray(athlete_ids),
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


def bootstrap_mae_delta_ci_clustered(
    err_baseline, err_adjusted, cluster_ids, n_boot=1000, seed=0, ci=0.95
):
    """Paired CLUSTER bootstrap of the MAE improvement (baseline - adjusted),
    resampling whole clusters (athletes) with replacement so within-player serial
    correlation is preserved. err_* and cluster_ids are aligned per-prediction
    arrays. Positive = adjusted has the LOWER (better) MAE. Returns
    (mean_delta, lo, hi)."""
    err_baseline = np.asarray(err_baseline, dtype=float)
    err_adjusted = np.asarray(err_adjusted, dtype=float)
    cluster_ids = np.asarray(cluster_ids)
    # map each unique cluster -> array of its row indices
    clusters = list(dict.fromkeys(cluster_ids.tolist()))
    idx_by_cluster = {c: np.flatnonzero(cluster_ids == c) for c in clusters}
    n_clusters = len(clusters)
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        draw = rng.integers(0, n_clusters, size=n_clusters)
        idx = np.concatenate([idx_by_cluster[clusters[j]] for j in draw])
        deltas[i] = err_baseline[idx].mean() - err_adjusted[idx].mean()
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(deltas, [alpha, 1.0 - alpha])
    return float(deltas.mean()), float(lo), float(hi)
