"""Tests for src/scoring/playoffs.py."""

import random

from src.scoring.monte_carlo import TeamStanding
from src.scoring.playoffs import (
    HOME_PATTERN_BO3,
    HOME_PATTERN_BO5,
    HOME_PATTERN_BO7,
    play_series,
    simulate_playoffs,  # noqa: F401  -- used in Task 2 tests added later
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
