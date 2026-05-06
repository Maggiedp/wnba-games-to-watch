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

# All 13 real WNBA teams, in order of fictional strength for the generic test.
_ALL_TEAMS = [
    "Atlanta Dream",
    "Chicago Sky",
    "Connecticut Sun",
    "Indiana Fever",
    "New York Liberty",
    "Washington Mystics",
    "Dallas Wings",
    "Golden State Valkyries",
    "Las Vegas Aces",
    "Los Angeles Sparks",
    "Minnesota Lynx",
    "Phoenix Mercury",
    "Seattle Storm",
]


def test_simulate_game_returns_bool():
    result = simulate_game(1600, 1400, home_advantage=0)
    assert isinstance(result, bool)


def test_run_monte_carlo_simulation():
    """13-team season — stronger teams should make the playoffs more often."""
    standings = {}
    for i, name in enumerate(_ALL_TEAMS):
        standings[name] = {
            "wins": 20 - i * 2,
            "losses": i,
            "elo": 1600 - i * 20,
        }

    remaining_games = [
        (_ALL_TEAMS[0], _ALL_TEAMS[1]),
        (_ALL_TEAMS[2], _ALL_TEAMS[3]),
        (_ALL_TEAMS[4], _ALL_TEAMS[5]),
    ]

    probs = run_monte_carlo_simulation(standings, remaining_games, num_simulations=1000)

    assert len(probs) == 13
    for _, prob in probs.items():
        assert 0.0 <= prob <= 1.0

    assert probs[_ALL_TEAMS[0]] >= probs[_ALL_TEAMS[12]]


def test_team_standing_has_h2h_default_empty():
    t = TeamStanding(name="X")
    assert t.h2h == {}


def test_run_monte_carlo_tracks_h2h_during_simulation():
    """After simulation, the playoff probabilities should still respond to
    completed-game H2H records — verify h2h is read from input standings dict.

    Fixture: 13 teams, no remaining games. 7 teams are safely in (18 wins each),
    then Connecticut Sun and New York Liberty are both tied at 10 wins (the 8th
    playoff spot), followed by 4 teams far out at 2 wins. Sun swept Liberty 3-0,
    so Sun should always win the H2H tiebreaker and claim the 8th seed.
    """
    # Use real WNBA team names so resolve_seeding can look up conferences.
    safe_in = [
        "Atlanta Dream",
        "Chicago Sky",
        "Indiana Fever",
        "Washington Mystics",
        "Dallas Wings",
        "Las Vegas Aces",
        "Minnesota Lynx",
    ]
    safe_out = [
        "Golden State Valkyries",
        "Los Angeles Sparks",
        "Phoenix Mercury",
        "Seattle Storm",
    ]

    standings = {}
    for name in safe_in:
        standings[name] = {"wins": 18, "losses": 6, "elo": 1550}

    # Connecticut Sun swept New York Liberty 3-0; both tied at 10 wins.
    standings["Connecticut Sun"] = {
        "wins": 10,
        "losses": 14,
        "elo": 1500,
        "h2h": {"New York Liberty": [3, 0]},
    }
    standings["New York Liberty"] = {
        "wins": 10,
        "losses": 14,
        "elo": 1500,
        "h2h": {"Connecticut Sun": [0, 3]},
    }

    for name in safe_out:
        standings[name] = {"wins": 2, "losses": 22, "elo": 1450}

    random.seed(42)
    # No remaining games — playoffs decided entirely by current standings + tiebreakers.
    # 7 safe-in teams lock the top 7 seeds.  Sun and Liberty compete for seed 8.
    # Sun swept Liberty 3-0 → Sun should win H2H tiebreaker every time.
    probs = run_monte_carlo_simulation(standings, [], num_simulations=200)
    assert probs["Connecticut Sun"] > probs["New York Liberty"], (
        f"Sun swept Liberty 3-0; Sun should win H2H tiebreaker, "
        f"got Sun={probs['Connecticut Sun']:.2f}, Liberty={probs['New York Liberty']:.2f}"
    )


def test_compute_standings_populates_h2h(monkeypatch):
    """Smoke test: standings dict produced by compute_standings has h2h field
    that's compatible with run_monte_carlo_simulation."""
    from unittest.mock import MagicMock
    from scripts.daily_update import compute_standings

    # Configure mocks to return string attributes instead of generating new mocks
    team_a = MagicMock()
    team_a.id = 1
    team_a.name = "New York Liberty"
    team_a.bpi_rating = 5.0

    team_b = MagicMock()
    team_b.id = 2
    team_b.name = "Las Vegas Aces"
    team_b.bpi_rating = 4.0

    game_1 = MagicMock()
    game_1.team_a_id = 1
    game_1.team_b_id = 2
    game_1.winner_id = 1

    game_2 = MagicMock()
    game_2.team_a_id = 2
    game_2.team_b_id = 1
    game_2.winner_id = 2

    game_3 = MagicMock()
    game_3.team_a_id = 1
    game_3.team_b_id = 2
    game_3.winner_id = 1

    # Patch at the module level where functions are imported
    monkeypatch.setattr(
        "scripts.daily_update.get_all_teams", lambda s: [team_a, team_b]
    )
    monkeypatch.setattr(
        "scripts.daily_update.get_completed_games",
        lambda s, season_year=2026: [game_1, game_2, game_3],
    )
    monkeypatch.setattr(
        "scripts.daily_update.get_team_by_id",
        lambda s, tid: {1: team_a, 2: team_b}[tid],
    )

    standings = compute_standings(session=None, elo_ratings={})

    # Liberty 2-1 vs Aces.
    assert standings["New York Liberty"]["h2h"]["Las Vegas Aces"] == [2, 1]
    assert standings["Las Vegas Aces"]["h2h"]["New York Liberty"] == [1, 2]
    assert standings["New York Liberty"]["wins"] == 2
    assert standings["Las Vegas Aces"]["wins"] == 1


# ---------------------------------------------------------------------------
# return_matrix tests (Task 1)
# ---------------------------------------------------------------------------

_S3 = {
    "Las Vegas Aces":   {"wins": 20, "losses": 5,  "elo": 1650, "h2h": {}},
    "New York Liberty": {"wins": 15, "losses": 10, "elo": 1500, "h2h": {}},
    "Indiana Fever":    {"wins": 5,  "losses": 20, "elo": 1350, "h2h": {}},
}
_G2 = [
    ("Las Vegas Aces", "New York Liberty"),
    ("New York Liberty", "Indiana Fever"),
]


def test_return_matrix_shape():
    """return_matrix=True yields a 3-tuple; matrix has shape (num_sims, num_games)."""
    import random; random.seed(0)
    result = run_monte_carlo_simulation(_S3, _G2, num_simulations=50, return_matrix=True)
    assert isinstance(result, tuple) and len(result) == 3
    probs, outcome_matrix, playoff_sets = result
    assert len(outcome_matrix) == 50
    assert all(len(row) == 2 for row in outcome_matrix)
    assert len(playoff_sets) == 50


def test_return_matrix_false_returns_dict():
    """Default (return_matrix=False) still returns a plain dict."""
    import random; random.seed(0)
    result = run_monte_carlo_simulation(_S3, _G2, num_simulations=50)
    assert isinstance(result, dict)


def test_return_matrix_probs_match_non_matrix():
    """Playoff probs from matrix mode match non-matrix mode within noise."""
    import random
    random.seed(42)
    probs_plain = run_monte_carlo_simulation(_S3, _G2, num_simulations=5000)
    random.seed(42)
    probs_matrix, _, _ = run_monte_carlo_simulation(_S3, _G2, num_simulations=5000, return_matrix=True)
    for name in _S3:
        assert abs(probs_plain[name] - probs_matrix[name]) < 0.03, (
            f"{name}: plain={probs_plain[name]:.3f} matrix={probs_matrix[name]:.3f}"
        )


def test_playoff_sets_are_sets_of_team_names():
    """Each playoff_set entry is a set of team name strings."""
    import random; random.seed(0)
    _, _, playoff_sets = run_monte_carlo_simulation(_S3, _G2, num_simulations=20, return_matrix=True)
    for s in playoff_sets:
        assert isinstance(s, set)
        for name in s:
            assert isinstance(name, str)
            assert name in _S3
