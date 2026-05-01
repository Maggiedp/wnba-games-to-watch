"""Tests for Monte Carlo simulation.

Win-probability math is tested in test_elo.py (the source of truth). These
tests cover the simulation wrapper — that it produces bools, that stronger
teams land in the playoffs more often, etc.
"""

import random

from src.scoring.monte_carlo import (
    TeamStanding,
    run_monte_carlo_simulation,
    simulate_game,
)


def test_simulate_game_returns_bool():
    result = simulate_game(1600, 1400, home_advantage=0)
    assert isinstance(result, bool)


def test_run_monte_carlo_simulation():
    """13-team synthetic season — stronger teams should make the playoffs more often."""
    standings = {}
    for i in range(13):
        standings[f"Team{i}"] = {
            "wins": 20 - i * 2,
            "losses": i,
            "elo": 1600 - i * 20,
        }

    remaining_games = [
        ("Team0", "Team1"),
        ("Team2", "Team3"),
        ("Team4", "Team5"),
    ]

    probs = run_monte_carlo_simulation(standings, remaining_games, num_simulations=1000)

    assert len(probs) == 13
    for _, prob in probs.items():
        assert 0.0 <= prob <= 1.0

    assert probs["Team0"] >= probs["Team12"]


def test_team_standing_has_h2h_default_empty():
    t = TeamStanding(name="X")
    assert t.h2h == {}


def test_run_monte_carlo_tracks_h2h_during_simulation():
    """After simulation, the playoff probabilities should still respond to
    completed-game H2H records — verify h2h is read from input standings dict."""
    standings = {
        "A": {"wins": 10, "losses": 5, "elo": 1600, "h2h": {"B": [3, 0]}},
        "B": {"wins": 10, "losses": 5, "elo": 1600, "h2h": {"A": [0, 3]}},
        "C": {"wins": 8, "losses": 7, "elo": 1500},
    }
    random.seed(42)
    # No remaining games — playoffs decided entirely by current standings + tiebreakers.
    # A and B tied on wins, but A swept B → A should make playoffs much more often.
    probs = run_monte_carlo_simulation(standings, [], num_simulations=200)
    assert probs["A"] > probs["B"], (
        f"A swept B 3-0; A should win H2H tiebreaker, got A={probs['A']:.2f}, B={probs['B']:.2f}"
    )
