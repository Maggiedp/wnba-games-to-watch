"""Tests for Monte Carlo simulation."""

from src.scoring.monte_carlo import (
    compute_win_probability,
    simulate_game,
    run_monte_carlo_simulation,
)


def test_compute_win_probability():
    """Test win probability calculation."""
    # Equal strength: 50%
    prob = compute_win_probability(0.0, 0.0)
    assert 0.45 < prob < 0.55

    # Team A stronger: > 50%
    prob = compute_win_probability(2.0, 0.0)
    assert prob > 0.5

    # Team B stronger: < 50%
    prob = compute_win_probability(0.0, 2.0)
    assert prob < 0.5


def test_simulate_game():
    """Test that simulation runs without error."""
    result = simulate_game(5.0, 3.0)
    assert isinstance(result, bool)


def test_run_monte_carlo_simulation():
    """Test Monte Carlo simulation."""
    # Create 13 teams (realistic WNBA scenario)
    standings = {}
    for i in range(13):
        standings[f"Team{i}"] = {
            "wins": 20 - i * 2,
            "losses": i,
            "bpi": 5 - i * 0.8,
        }

    remaining_games = [
        ("Team0", "Team1"),
        ("Team2", "Team3"),
        ("Team4", "Team5"),
    ]

    probs = run_monte_carlo_simulation(standings, remaining_games, num_simulations=1000)

    # All teams should have a probability
    assert len(probs) == 13
    for team, prob in probs.items():
        assert 0.0 <= prob <= 1.0

    # Strong teams should have higher or equal probability than weak teams
    assert probs["Team0"] >= probs["Team12"]
