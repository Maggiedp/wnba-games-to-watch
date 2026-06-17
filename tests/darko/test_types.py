from src.darko.types import PlayerImpact


def test_player_impact_total_is_off_plus_def():
    pi = PlayerImpact(player_id=42, off=3.0, def_=-1.5, off_sd=0.8, def_sd=0.9)
    assert pi.total == 1.5
    assert pi.player_id == 42
