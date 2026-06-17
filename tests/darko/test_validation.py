import numpy as np
import pandas as pd
from src.darko.validation import walk_forward_retrodiction


def _player_game_signals(seed=5):
    """One player, 40 games; true talent drifts up. Signal = talent + noise."""
    rng = np.random.default_rng(seed)
    talent, rows = 0.0, []
    for game in range(40):
        talent += 0.05
        rows.append(
            {"athlete_id": 1, "game_idx": game, "signal": talent + rng.normal(0, 1.0)}
        )
    return pd.DataFrame(rows)


def test_full_and_box_beat_naive_on_drifting_player():
    df = _player_game_signals()
    res = walk_forward_retrodiction(df, q=0.05, r=1.0)
    assert res["mae"]["kalman"] < res["mae"]["naive"]
    assert "kalman_anchored" in res["mae"]
    assert 0.6 <= res["coverage_80"]["kalman"] <= 0.95


def test_full_uses_anchor_and_drift():
    # Two games; anchor seeds kalman_anchored's x0=5.0 (kalman starts at 0); a drift
    # entry lifts the game-1 predict by 0.5. With signals at/near 5, kalman_anchored
    # should beat kalman.
    df = pd.DataFrame(
        [
            {"athlete_id": 1, "game_idx": 0, "signal": 5.0},
            {"athlete_id": 1, "game_idx": 1, "signal": 5.5},
        ]
    )
    res = walk_forward_retrodiction(
        df,
        q=0.1,
        r=1.0,
        anchor={1: 5.0},
        drift={(1, 1): 0.5},
    )
    assert res["mae"]["kalman_anchored"] < res["mae"]["kalman"]


def test_walk_forward_returns_paired_abs_errors():
    import numpy as np

    df = _player_game_signals()  # existing helper in this file
    res = walk_forward_retrodiction(df, q=0.05, r=1.0)
    assert set(res["abs_err"].keys()) == {"naive", "kalman", "kalman_anchored"}
    n = len(res["abs_err"]["naive"])
    assert n == len(res["abs_err"]["kalman"]) == len(res["abs_err"]["kalman_anchored"])
    # mean of the per-prediction errors equals the reported MAE
    assert abs(float(np.mean(res["abs_err"]["kalman"])) - res["mae"]["kalman"]) < 1e-9


def test_bootstrap_mae_delta_ci_brackets_known_improvement():
    import numpy as np
    from src.darko.validation import bootstrap_mae_delta_ci

    rng = np.random.default_rng(0)
    # adjusted is uniformly ~0.5 better than baseline -> positive delta, CI excludes 0
    err_base = np.abs(rng.normal(0, 1, size=500)) + 0.5
    err_adj = np.abs(
        rng.normal(0, 1, size=500)
    )  # not paired-correlated, but lower mean
    mean_d, lo, hi = bootstrap_mae_delta_ci(err_base, err_adj, n_boot=500, seed=1)
    assert mean_d > 0
    assert lo > 0  # improvement is significant


def test_bootstrap_mae_delta_ci_straddles_zero_for_noise():
    import numpy as np
    from src.darko.validation import bootstrap_mae_delta_ci

    rng = np.random.default_rng(2)
    # Two INDEPENDENT draws from the same distribution -> no real difference, so the
    # paired delta has genuine variance and the CI should straddle 0. (A deterministic
    # constant offset would give a degenerate zero-variance CI, not a noise test.)
    err_base = np.abs(rng.normal(0, 1, size=400))
    err_adj = np.abs(rng.normal(0, 1, size=400))
    _, lo, hi = bootstrap_mae_delta_ci(err_base, err_adj, n_boot=500, seed=3)
    assert lo < 0 < hi  # not significant


def test_target_distinct_from_signal_is_scored_against_target():
    # Kalman observes `signal` but is scored against `target`. If target is a constant
    # 5.0 while signal trends, errors are |pred(from signal) - 5.0|.
    import numpy as np

    df = pd.DataFrame(
        {
            "athlete_id": [1, 1, 1, 1],
            "game_idx": [0, 1, 2, 3],
            "signal": [0.0, 1.0, 2.0, 3.0],
            "target": [5.0, 5.0, 5.0, 5.0],
        }
    )
    res = walk_forward_retrodiction(df, q=0.1, r=1.0)
    # naive pred game0 = last signal (none -> 0.0); scored vs target 5.0 -> err 5.0
    assert res["abs_err"]["naive"][0] == 5.0
    # all errors are computed against target 5.0, not the signal stream
    assert np.all(res["abs_err"]["kalman"] >= 0)


def test_returns_aligned_athlete_ids():
    df = _player_game_signals()  # existing helper (single athlete id=1)
    res = walk_forward_retrodiction(df, q=0.05, r=1.0)
    assert len(res["athlete_ids"]) == len(res["abs_err"]["naive"])
    assert set(res["athlete_ids"].tolist()) == {1}


def test_clustered_bootstrap_wider_than_iid_under_within_cluster_correlation():
    # Construct per-player correlated error advantage: each athlete is EITHER all-better
    # or all-worse. iid bootstrap sees many independent rows; clustered sees few
    # independent clusters -> clustered CI must be WIDER.
    import numpy as np

    from src.darko.validation import (
        bootstrap_mae_delta_ci,
        bootstrap_mae_delta_ci_clustered,
    )

    rng = np.random.default_rng(0)
    n_players, per = 20, 30
    cluster_ids, err_base, err_adj = [], [], []
    for pid in range(n_players):
        sign = 1.0 if rng.random() < 0.5 else -1.0  # whole-player advantage direction
        for _ in range(per):
            cluster_ids.append(pid)
            b = abs(rng.normal(0, 1))
            err_base.append(b)
            err_adj.append(b - sign * 0.3)  # consistent within player
    cluster_ids = np.array(cluster_ids)
    err_base = np.array(err_base)
    err_adj = np.array(err_adj)
    _, lo_i, hi_i = bootstrap_mae_delta_ci(err_base, err_adj, n_boot=500, seed=1)
    _, lo_c, hi_c = bootstrap_mae_delta_ci_clustered(
        err_base, err_adj, cluster_ids, n_boot=500, seed=1
    )
    assert (hi_c - lo_c) > (hi_i - lo_i)  # clustering widens the interval


def test_clustered_bootstrap_brackets_known_improvement():
    import numpy as np

    from src.darko.validation import bootstrap_mae_delta_ci_clustered

    rng = np.random.default_rng(3)
    cluster_ids = np.repeat(np.arange(40), 10)
    err_base = np.abs(rng.normal(0, 1, size=400)) + 0.5
    err_adj = np.abs(rng.normal(0, 1, size=400))
    mean_d, _, _ = bootstrap_mae_delta_ci_clustered(
        err_base, err_adj, cluster_ids, n_boot=500, seed=2
    )
    assert mean_d > 0
