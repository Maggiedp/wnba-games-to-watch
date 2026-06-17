import pandas as pd
from src.darko.stints import on_court_snapshots


def _tiny_game():
    # 1 game, 2 teams (100/200), 5 starters each, one substitution.
    box = pd.DataFrame(
        {
            "game_id": [1] * 10,
            "athlete_id": list(range(1, 11)),
            "team_id": [100] * 5 + [200] * 5,
            "starter": [True] * 5 + [True] * 5,
            "minutes": [20.0] * 10,
        }
    )
    pbp = pd.DataFrame(
        {
            "game_id": [1, 1],
            "sequence_number": [1, 2],
            "type_text": ["Jump Ball", "Substitution"],
            "team_id": [100, 100],
            "athlete_id_1": [None, 11],  # player 11 comes in
            "athlete_id_2": [None, 1],  # player 1 goes out
        }
    )
    # player 11 must exist in box for the 5-count to stay valid after the sub
    box.loc[len(box)] = [1, 11, 100, False, 5.0]
    return pbp, box


def test_snapshots_keep_five_per_team():
    pbp, box = _tiny_game()
    snaps = on_court_snapshots(pbp, box, game_id=1)
    assert len(snaps) == 2
    for snap in snaps:
        assert len(snap) == 2  # two teams
        assert all(len(players) == 5 for players in snap.values())
    # after the sub, team 100 has 11 not 1
    assert 11 in snaps[1][100] and 1 not in snaps[1][100]
