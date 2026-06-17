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
    }
