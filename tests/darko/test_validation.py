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
    assert res["mae"]["box_only"] < res["mae"]["naive"]
    assert "full" in res["mae"]
    assert 0.6 <= res["coverage_80"]["box_only"] <= 0.95


def test_full_uses_anchor_and_drift():
    # Two games; anchor seeds full's x0=5.0 (box_only starts at 0); a drift entry
    # lifts the game-1 predict by 0.5. With signals at/near 5, full should beat box.
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
    assert res["mae"]["full"] < res["mae"]["box_only"]


def test_walk_forward_returns_paired_abs_errors():
    import numpy as np

    df = _player_game_signals()  # existing helper in this file
    res = walk_forward_retrodiction(df, q=0.05, r=1.0)
    assert set(res["abs_err"].keys()) == {"naive", "box_only", "full"}
    n = len(res["abs_err"]["naive"])
    assert n == len(res["abs_err"]["box_only"]) == len(res["abs_err"]["full"])
    # mean of the per-prediction errors equals the reported MAE
    assert (
        abs(float(np.mean(res["abs_err"]["box_only"])) - res["mae"]["box_only"]) < 1e-9
    )


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
