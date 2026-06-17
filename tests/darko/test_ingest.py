import pandas as pd
from src.darko.ingest import load_player_box


def test_load_player_box_reads_cached_parquet(tmp_path):
    # Seed a fake cached parquet so no network call happens.
    cache = tmp_path / "cache"
    cache.mkdir()
    df = pd.DataFrame(
        {
            "game_id": [1, 1],
            "athlete_id": [10, 11],
            "team_id": [100, 100],
            "starter": [True, False],
            "minutes": [30.0, 18.0],
            "season": [2024, 2024],
        }
    )
    df.to_parquet(cache / "player_box_2024.parquet")

    out = load_player_box(2024, cache_dir=str(cache))
    assert list(out["athlete_id"]) == [10, 11]
    assert out.attrs.get("season") == 2024
