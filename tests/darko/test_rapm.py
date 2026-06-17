import numpy as np
import pandas as pd
from src.darko.rapm import fit_rapm

PLAYERS = list(range(1, 13))  # 12 players -> two 6-man rotations


def _synthetic_stints(n=4000, seed=1):
    rng = np.random.default_rng(seed)
    true_off = {p: rng.normal(0, 2) for p in PLAYERS}
    true_def = {p: rng.normal(0, 2) for p in PLAYERS}
    rows = []
    for _ in range(n):
        team_a = set(rng.choice(PLAYERS[:6], 5, replace=False))
        team_b = set(rng.choice(PLAYERS[6:], 5, replace=False))
        off, deff = (team_a, team_b) if rng.random() < 0.5 else (team_b, team_a)
        rating = 100 + sum(true_off[p] for p in off) - sum(true_def[p] for p in deff)
        poss = rng.integers(2, 8)
        pts = max(0, rng.normal(rating / 100 * poss, 1.0))
        rows.append(
            {
                "off_players": frozenset(off),
                "def_players": frozenset(deff),
                "possessions": float(poss),
                "points": float(pts),
                "weight": 1.0,
            }
        )
    return pd.DataFrame(rows), true_off, true_def


def test_rapm_recovers_relative_ordering():
    stints, true_off, true_def = _synthetic_stints()
    prior = pd.Series({p: 0.0 for p in PLAYERS})
    impacts = fit_rapm(stints, prior_off=prior, prior_def=prior, lam=1.0)
    est = {pi.player_id: pi.off for pi in impacts}
    # rank correlation between estimated and true offensive impact is strong
    order_true = sorted(PLAYERS, key=lambda p: true_off[p])
    order_est = sorted(PLAYERS, key=lambda p: est[p])
    agree = sum(a == b for a, b in zip(order_true, order_est))
    assert agree >= len(PLAYERS) // 2


def test_rapm_scales_without_dense_weight_matrix():
    # 20k rows would be a 20k x 20k (~3GB) np.diag -> must NOT be formed.
    stints, _, _ = _synthetic_stints(n=20000, seed=2)
    prior = pd.Series({p: 0.0 for p in PLAYERS})
    impacts = fit_rapm(stints, prior_off=prior, prior_def=prior, lam=1.0)
    assert len(impacts) == len(PLAYERS)
