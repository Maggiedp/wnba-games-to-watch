"""Tests for Monte Carlo simulation.

Win-probability math is tested in test_elo.py (the source of truth). These
tests cover the simulation wrapper — that it produces bools, that stronger
teams land in the playoffs more often, etc.
"""

from src.scoring.monte_carlo import run_monte_carlo_simulation, simulate_game


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
