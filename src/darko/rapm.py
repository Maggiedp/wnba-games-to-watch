"""Regularized ridge RAPM. Prior mean = box prior; estimate is shrunk toward it,
so thin/collinear stints fall back to box. Pool all seasons. Offense and defense
each get a per-player coefficient; defense is modeled with -1 columns so the solved
coefficient is already +points-prevented (higher = better defense)."""

import numpy as np
import pandas as pd
from src.darko.types import PlayerImpact


def fit_rapm(
    stints: pd.DataFrame, prior_off: pd.Series, prior_def: pd.Series, lam: float = 1.0
) -> list[PlayerImpact]:
    players = sorted(set().union(*stints["off_players"], *stints["def_players"]))
    idx = {p: i for i, p in enumerate(players)}
    n_p = len(players)
    n = len(stints)

    # Design: columns [offense(n_p) | defense(n_p)]. +1 offense, -1 defense
    # (a defender lowers the offense's points, so a -1 column yields a coefficient
    # in +points-prevented units — reported directly, no output flip).
    X = np.zeros((n, 2 * n_p))
    y = np.zeros(n)
    w = (
        stints["weight"].to_numpy(dtype=float)
        if "weight" in stints.columns
        else np.ones(n)
    )
    w = w * stints["possessions"].to_numpy(dtype=float)
    for r, (_, row) in enumerate(stints.iterrows()):
        for p in row["off_players"]:
            X[r, idx[p]] = 1.0
        for p in row["def_players"]:
            X[r, n_p + idx[p]] = -1.0
        y[r] = 100.0 * row["points"] / row["possessions"]

    # Defense columns are -1, so a defender's solved coefficient already equals
    # +points-prevented (a -1 column times a coefficient that is the negative of
    # the points it removes from y). The box prior for defense is also in
    # +points-prevented units, so the prior mean enters with a + sign and the
    # output coefficient is reported as-is. (Negating both — prior mean and
    # output — double-flips and inverts every defender's sign.)
    mu = np.array(
        [prior_off.get(p, 0.0) for p in players]
        + [prior_def.get(p, 0.0) for p in players]
    )
    # Weighted ridge via broadcasting (NEVER form np.diag(w) — it's n x n).
    Xw = w[:, None] * X
    A = X.T @ Xw + lam * np.eye(2 * n_p)
    b = X.T @ (w * y) + lam * mu
    beta = np.linalg.solve(A, b)
    # SD scales by residual variance from the ridge posterior covariance diagonal
    cov = np.linalg.inv(A)
    resid_var = float(np.mean((X @ beta - y) ** 2))
    sd = np.sqrt(np.clip(np.diag(cov), 0, None) * resid_var)

    out = []
    for p in players:
        i = idx[p]
        out.append(
            PlayerImpact(
                player_id=int(p),
                off=float(beta[i]),
                def_=float(beta[n_p + i]),  # already +points-prevented; higher = better
                off_sd=float(sd[i]),
                def_sd=float(sd[n_p + i]),
            )
        )
    return out
