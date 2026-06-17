import pandas as pd
from src.darko.stints import minutes_discrepancy, game_reliability


def test_minutes_discrepancy_flags_overrun():
    # box says player 1 played 600s; reconstruction says 1200s -> discrepancy.
    box = pd.DataFrame({"game_id": [1], "athlete_id": [1], "minutes": [10.0]})
    recon_seconds = {1: 1200.0}
    disc = minutes_discrepancy(recon_seconds, box, game_id=1)
    assert disc[1] == 600.0  # 1200 - 600


def test_game_reliability_fraction_within_tolerance():
    box = pd.DataFrame(
        {
            "game_id": [1, 1],
            "athlete_id": [1, 2],
            "minutes": [10.0, 10.0],  # 600s each
        }
    )
    recon_seconds = {1: 610.0, 2: 1000.0}  # player1 within 75s tol, player2 not
    assert game_reliability(recon_seconds, box, game_id=1) == 0.5
