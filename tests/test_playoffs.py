"""Tests for src/scoring/playoffs.py."""

import random

from src.scoring.monte_carlo import TeamStanding
from src.scoring.playoffs import (
    HOME_PATTERN_BO3,
    HOME_PATTERN_BO5,
    HOME_PATTERN_BO7,
    play_series,
    simulate_playoffs,
)


def _standings(elos: dict[str, float]) -> dict[str, TeamStanding]:
    return {n: TeamStanding(name=n, elo=e) for n, e in elos.items()}


def test_play_series_bo3_higher_seed_dominant_wins():
    """Higher seed with massive Elo edge should win the series ~always."""
    random.seed(1)
    s = _standings({"H": 1800, "L": 1200})
    wins = sum(play_series("H", "L", HOME_PATTERN_BO3, s) == "H" for _ in range(200))
    assert wins >= 190


def test_play_series_short_circuits_at_majority():
    """A Bo3 should never call random.random() more than 3 times."""
    s = _standings({"H": 1500, "L": 1500})

    call_count = {"n": 0}
    real_random = random.random

    def counting_random():
        call_count["n"] += 1
        return real_random()

    random.random = counting_random
    try:
        play_series("H", "L", HOME_PATTERN_BO3, s)
    finally:
        random.random = real_random

    assert call_count["n"] <= 3


def test_play_series_returns_one_of_the_two_teams():
    s = _standings({"H": 1500, "L": 1500})
    random.seed(42)
    for _ in range(50):
        winner = play_series("H", "L", HOME_PATTERN_BO5, s)
        assert winner in ("H", "L")


def test_play_series_home_pattern_constants_have_correct_lengths():
    assert len(HOME_PATTERN_BO3) == 3
    assert len(HOME_PATTERN_BO5) == 5
    assert len(HOME_PATTERN_BO7) == 7
    for pattern in (HOME_PATTERN_BO3, HOME_PATTERN_BO5, HOME_PATTERN_BO7):
        for entry in pattern:
            assert entry in ("H", "L")


def test_play_series_lower_seed_hosts_advantage_applied():
    """When the lower seed hosts a neutral-elo matchup, they should win
    those games more often than 50% (home court).
    """
    random.seed(7)
    s = _standings({"H": 1500, "L": 1500})

    lower_wins = sum(play_series("H", "L", ("L",), s) == "L" for _ in range(2000))
    assert lower_wins > 1050  # > 52.5%, well above 50/50 noise at N=2000


SEEDS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]


def _equal_standings() -> dict[str, "TeamStanding"]:
    return _standings({s: 1500 for s in SEEDS})


def test_simulate_playoffs_returns_required_keys():
    random.seed(0)
    result = simulate_playoffs(SEEDS, _equal_standings())
    assert set(result.keys()) == {
        "made_playoffs",
        "reached_semis",
        "reached_finals",
        "won_championship",
    }


def test_simulate_playoffs_cardinalities():
    """Each round has the expected number of teams."""
    random.seed(0)
    result = simulate_playoffs(SEEDS, _equal_standings())
    assert len(result["made_playoffs"]) == 8
    assert len(result["reached_semis"]) == 4
    assert len(result["reached_finals"]) == 2
    # won_championship is a single team name, not a set.
    assert isinstance(result["won_championship"], str)


def test_simulate_playoffs_subset_chain():
    """champion ∈ finals ⊂ semis ⊂ made_playoffs."""
    random.seed(0)
    for _ in range(20):
        result = simulate_playoffs(SEEDS, _equal_standings())
        assert result["won_championship"] in result["reached_finals"]
        assert result["reached_finals"].issubset(result["reached_semis"])
        assert result["reached_semis"].issubset(result["made_playoffs"])


def test_simulate_playoffs_made_playoffs_is_input_seeds():
    """made_playoffs should be exactly the seeded list (top 8)."""
    random.seed(0)
    result = simulate_playoffs(SEEDS, _equal_standings())
    assert result["made_playoffs"] == set(SEEDS)


def test_simulate_playoffs_top_seed_wins_against_weak_field():
    """Seed 1 with massive Elo edge should usually win the championship."""
    elos = {"S1": 1900}
    for s in SEEDS[1:]:
        elos[s] = 1300
    s = _standings(elos)
    random.seed(123)
    champ_wins = sum(
        simulate_playoffs(SEEDS, s)["won_championship"] == "S1" for _ in range(200)
    )
    assert champ_wins >= 170  # > 85%
