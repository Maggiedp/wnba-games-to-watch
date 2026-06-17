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
