"""Tests for Monte Carlo simulation.

Win-probability math is tested in test_elo.py (the source of truth). These
tests cover the simulation wrapper — that it produces bools, that stronger
teams land in the playoffs more often, etc.
"""

import random

import pytest

from src.scoring.monte_carlo import (
    RoundProbabilities,
    TeamStanding,
    compute_importance_from_matrix,
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

    result = run_monte_carlo_simulation(standings, remaining_games, num_simulations=1000)
    assert len(result.make_playoffs) == 13
    for _, prob in result.make_playoffs.items():
        assert 0.0 <= prob <= 1.0
    assert result.make_playoffs[_ALL_TEAMS[0]] >= result.make_playoffs[_ALL_TEAMS[12]]


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
    probs = run_monte_carlo_simulation(standings, [], num_simulations=200).make_playoffs
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


def test_compute_standings_ignores_postseason_completions(monkeypatch):
    """Completed postseason games (season_type=3) must not bump regular-season W/L
    or H2H. Otherwise playoff wins would distort seeding mid-postseason."""
    from unittest.mock import MagicMock
    from scripts.daily_update import compute_standings

    team_a = MagicMock(id=1, bpi_rating=5.0)
    team_a.name = "New York Liberty"
    team_b = MagicMock(id=2, bpi_rating=4.0)
    team_b.name = "Las Vegas Aces"

    # Regular-season: Liberty wins.
    reg = MagicMock(team_a_id=1, team_b_id=2, winner_id=1, season_type=2)
    # Postseason: Aces win — must be ignored.
    post = MagicMock(team_a_id=2, team_b_id=1, winner_id=2, season_type=3)

    monkeypatch.setattr(
        "scripts.daily_update.get_all_teams", lambda s: [team_a, team_b]
    )
    monkeypatch.setattr(
        "scripts.daily_update.get_completed_games",
        lambda s, season_year=2026: [reg, post],
    )
    monkeypatch.setattr(
        "scripts.daily_update.get_team_by_id",
        lambda s, tid: {1: team_a, 2: team_b}[tid],
    )

    standings = compute_standings(session=None, elo_ratings={})

    # Liberty 1-0, Aces 0-1 — postseason game does not contribute.
    assert standings["New York Liberty"]["wins"] == 1
    assert standings["New York Liberty"]["losses"] == 0
    assert standings["Las Vegas Aces"]["wins"] == 0
    assert standings["Las Vegas Aces"]["losses"] == 1
    assert standings["New York Liberty"]["h2h"]["Las Vegas Aces"] == [1, 0]


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
    round_probs, outcome_matrix, playoff_sets = result
    assert isinstance(round_probs, RoundProbabilities)
    assert len(outcome_matrix) == 50
    assert all(len(row) == 2 for row in outcome_matrix)
    assert len(playoff_sets) == 50


def test_return_matrix_false_returns_round_probabilities():
    """Without return_matrix, returns RoundProbabilities directly."""
    import random; random.seed(0)
    result = run_monte_carlo_simulation(_S3, _G2, num_simulations=50)
    assert isinstance(result, RoundProbabilities)


def test_return_matrix_probs_match_non_matrix():
    """Playoff probs from matrix mode match non-matrix mode within noise."""
    import random
    random.seed(42)
    probs_plain = run_monte_carlo_simulation(_S3, _G2, num_simulations=5000)
    random.seed(42)
    probs_matrix, _, _ = run_monte_carlo_simulation(_S3, _G2, num_simulations=5000, return_matrix=True)
    for name in probs_plain.make_playoffs:
        assert probs_plain.make_playoffs[name] == probs_matrix.make_playoffs[name]


def test_playoff_sets_are_sets_of_team_names():
    """Each playoff_set entry is a set of team name strings."""
    import random; random.seed(0)
    _, _, playoff_sets = run_monte_carlo_simulation(_S3, _G2, num_simulations=20, return_matrix=True)
    for s in playoff_sets:
        assert isinstance(s, set)
        for name in s:
            assert isinstance(name, str)
            assert name in _S3


# ---------------------------------------------------------------------------
# compute_importance_from_matrix tests (Task 2)
# ---------------------------------------------------------------------------

# Minimal bubble fixture for matrix importance tests
_BUBBLE = {
    "Las Vegas Aces":         {"wins": 28, "losses": 7,  "elo": 1650, "h2h": {}},
    "New York Liberty":       {"wins": 26, "losses": 9,  "elo": 1620, "h2h": {}},
    "Minnesota Lynx":         {"wins": 24, "losses": 11, "elo": 1590, "h2h": {}},
    "Indiana Fever":          {"wins": 22, "losses": 13, "elo": 1560, "h2h": {}},
    "Connecticut Sun":        {"wins": 20, "losses": 15, "elo": 1530, "h2h": {}},
    "Seattle Storm":          {"wins": 19, "losses": 16, "elo": 1515, "h2h": {}},
    "Atlanta Dream":          {"wins": 18, "losses": 17, "elo": 1500, "h2h": {}},
    "Chicago Sky":            {"wins": 18, "losses": 17, "elo": 1485, "h2h": {}},
    "Washington Mystics":     {"wins": 18, "losses": 17, "elo": 1470, "h2h": {}},
    "Dallas Wings":           {"wins": 18, "losses": 17, "elo": 1455, "h2h": {}},
    "Golden State Valkyries": {"wins": 14, "losses": 21, "elo": 1410, "h2h": {}},
    "Los Angeles Sparks":     {"wins": 10, "losses": 25, "elo": 1350, "h2h": {}},
    "Phoenix Mercury":        {"wins": 7,  "losses": 28, "elo": 1320, "h2h": {}},
}
_BUBBLE_GAMES = [
    ("Atlanta Dream",      "Chicago Sky"),        # index 0 — bubble vs bubble
    ("Washington Mystics", "Dallas Wings"),        # index 1 — bubble vs bubble
    ("Las Vegas Aces",     "New York Liberty"),    # index 2 — safely in vs safely in
    ("Minnesota Lynx",     "Indiana Fever"),       # index 3
    ("Connecticut Sun",    "Seattle Storm"),       # index 4
]


def test_compute_importance_from_matrix_length():
    """Returns one swing value per remaining game."""
    import random; random.seed(0)
    _, outcome_matrix, playoff_sets = run_monte_carlo_simulation(
        _BUBBLE, _BUBBLE_GAMES, num_simulations=200, return_matrix=True
    )
    swings = compute_importance_from_matrix(outcome_matrix, playoff_sets, _BUBBLE_GAMES, list(_BUBBLE.keys()))
    assert len(swings) == len(_BUBBLE_GAMES)


def test_compute_importance_from_matrix_non_negative():
    """All swing values are >= 0."""
    import random; random.seed(0)
    _, outcome_matrix, playoff_sets = run_monte_carlo_simulation(
        _BUBBLE, _BUBBLE_GAMES, num_simulations=500, return_matrix=True
    )
    swings = compute_importance_from_matrix(outcome_matrix, playoff_sets, _BUBBLE_GAMES, list(_BUBBLE.keys()))
    assert all(s >= 0.0 for s in swings)


def test_compute_importance_from_matrix_bubble_beats_safe():
    """Bubble game (index 0) has higher swing than safely-in game (index 2)."""
    import random; random.seed(42)
    _, outcome_matrix, playoff_sets = run_monte_carlo_simulation(
        _BUBBLE, _BUBBLE_GAMES, num_simulations=5000, return_matrix=True
    )
    swings = compute_importance_from_matrix(outcome_matrix, playoff_sets, _BUBBLE_GAMES, list(_BUBBLE.keys()))
    assert swings[0] > swings[2], f"bubble={swings[0]:.3f} safe={swings[2]:.3f}"


def test_compute_importance_from_matrix_empty_subset_returns_zero():
    """When all sims agree on the outcome (degenerate case), swing is 0."""
    # Construct a matrix where team_a always wins game 0
    outcome_matrix = [[True, False]] * 100
    playoff_sets = [{"Las Vegas Aces", "New York Liberty"} for _ in range(100)]
    games = [("Las Vegas Aces", "Indiana Fever"), ("New York Liberty", "Indiana Fever")]
    swings = compute_importance_from_matrix(outcome_matrix, playoff_sets, games, ["Las Vegas Aces", "New York Liberty", "Indiana Fever"])
    assert swings[0] == 0.0  # b_won is empty — no split possible


# ---------------------------------------------------------------------------
# RoundProbabilities tests (Task 3)
# ---------------------------------------------------------------------------


def test_run_monte_carlo_returns_round_probabilities():
    """Return type is now RoundProbabilities, not a plain dict."""
    standings = {}
    for i, name in enumerate(_ALL_TEAMS):
        standings[name] = {
            "wins": 20 - i * 2,
            "losses": i,
            "elo": 1600 - i * 20,
        }
    result = run_monte_carlo_simulation(standings, [], num_simulations=200)
    assert isinstance(result, RoundProbabilities)
    assert set(result.make_playoffs.keys()) == set(_ALL_TEAMS)
    assert set(result.reach_semis.keys()) == set(_ALL_TEAMS)
    assert set(result.reach_finals.keys()) == set(_ALL_TEAMS)
    assert set(result.win_championship.keys()) == set(_ALL_TEAMS)


def test_run_monte_carlo_round_probability_sums():
    """Each round contributes its team count per sim, so summed probability
    across teams equals the round size (8 / 4 / 2 / 1)."""
    standings = {}
    for i, name in enumerate(_ALL_TEAMS):
        standings[name] = {
            "wins": 20 - i * 2,
            "losses": i,
            "elo": 1600 - i * 20,
        }
    result = run_monte_carlo_simulation(standings, [], num_simulations=500)
    assert sum(result.make_playoffs.values()) == pytest.approx(8.0, abs=0.01)
    assert sum(result.reach_semis.values()) == pytest.approx(4.0, abs=0.01)
    assert sum(result.reach_finals.values()) == pytest.approx(2.0, abs=0.01)
    assert sum(result.win_championship.values()) == pytest.approx(1.0, abs=0.01)


def test_run_monte_carlo_round_probabilities_are_monotone():
    """For any team, P(make_playoffs) >= P(reach_semis) >= P(reach_finals) >= P(win_championship)."""
    standings = {}
    for i, name in enumerate(_ALL_TEAMS):
        standings[name] = {
            "wins": 20 - i * 2,
            "losses": i,
            "elo": 1600 - i * 20,
        }
    result = run_monte_carlo_simulation(standings, [], num_simulations=500)
    for name in _ALL_TEAMS:
        mp = result.make_playoffs[name]
        sf = result.reach_semis[name]
        fn = result.reach_finals[name]
        ch = result.win_championship[name]
        assert mp + 1e-9 >= sf
        assert sf + 1e-9 >= fn
        assert fn + 1e-9 >= ch


# ---------------------------------------------------------------------------
# Importance-rule per game type
# ---------------------------------------------------------------------------


def test_importance_for_game_postseason_is_max():
    """Playoff games are all championship-stakes — must score max importance."""
    from scripts.daily_update import _importance_for_game

    game = {"team_a": "X", "team_b": "Y", "event_id": "e1", "season_type": 3}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75
    )
    assert result == 100.0


def test_importance_for_game_preseason_is_zero():
    from scripts.daily_update import _importance_for_game

    game = {"team_a": "X", "team_b": "Y", "event_id": "e2", "season_type": 1}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75
    )
    assert result == 0.0


def test_importance_for_game_regular_season_uses_indexed_swing():
    """Regular season game with event in sim universe normalizes the raw swing."""
    from scripts.daily_update import _importance_for_game

    game = {"team_a": "X", "team_b": "Y", "event_id": "e3", "season_type": 2}
    # raw_swings[0] = 0.375, ceiling 0.75 → 50/100.
    result = _importance_for_game(
        game,
        raw_swings=[0.375],
        remaining_event_index={"e3": 0},
        importance_ceiling=0.75,
    )
    assert abs(result - 50.0) < 1e-6


def test_importance_for_game_regular_season_unindexed_returns_none():
    """Regular season game not in sim universe (e.g. preseason filter race) → None."""
    from scripts.daily_update import _importance_for_game

    game = {"team_a": "X", "team_b": "Y", "event_id": "e_missing", "season_type": 2}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75
    )
    assert result is None


def test_compute_daily_scores_no_upcoming_games_still_produces_round_probs(monkeypatch):
    """Legitimate end-of-season state: completed games exist but nothing's
    upcoming. Playoff picture should still update — locked seeding + bracket
    sim produce meaningful round probabilities even with zero remaining games.
    """
    from scripts.daily_update import compute_daily_scores

    real_names = _ALL_TEAMS
    standings = {
        n: {"wins": 30 - i, "losses": i, "bpi": 0.0, "elo": 1600 - i * 20, "h2h": {}}
        for i, n in enumerate(real_names)
    }
    # A single completed FINAL game distinguishes "season ended" from
    # "ESPN fetch failed". Date is in the past so no game is upcoming.
    games = [
        {
            "team_a": real_names[0],
            "team_b": real_names[1],
            "date": "2026-04-01",
            "status": "STATUS_FINAL",
            "season_type": 2,
            "event_id": "e1",
        }
    ]
    # Bypass the DB-dependent bracket-state build; this test is about the
    # no-upcoming-games path, not bracket reconstruction.
    monkeypatch.setattr(
        "scripts.daily_update._build_current_bracket_state",
        lambda session, standings: None,
    )

    scored, round_probs = compute_daily_scores(
        session=None, games=games, standings=standings
    )

    assert scored == []
    # All 13 teams represented; 8 of them have nonzero make_playoffs odds.
    assert len(round_probs.make_playoffs) == len(real_names)
    nonzero = sum(1 for v in round_probs.make_playoffs.values() if v > 0)
    assert nonzero >= 8
    # The bracket sim ran: each round's per-team probabilities sum to round size.
    assert sum(round_probs.make_playoffs.values()) == pytest.approx(8.0, abs=0.01)
    assert sum(round_probs.win_championship.values()) == pytest.approx(1.0, abs=0.01)


def test_compute_daily_scores_empty_games_returns_empty(monkeypatch):
    """ESPN fetch failure (games=[]) must not overwrite today's playoff
    probabilities with synthetic end-of-season odds. Returns empty round
    probs so the upsert path is a no-op and yesterday's record stays."""
    from scripts.daily_update import compute_daily_scores

    standings = {
        n: {"wins": 30 - i, "losses": i, "bpi": 0.0, "elo": 1600 - i * 20, "h2h": {}}
        for i, n in enumerate(_ALL_TEAMS)
    }
    monkeypatch.setattr(
        "scripts.daily_update._build_current_bracket_state",
        lambda session, standings: None,
    )
    scored, round_probs = compute_daily_scores(
        session=None, games=[], standings=standings
    )
    assert scored == []
    # Empty RoundProbabilities — no entries, store_playoff_probabilities is a no-op.
    assert round_probs.make_playoffs == {}
    assert round_probs.reach_semis == {}
    assert round_probs.reach_finals == {}
    assert round_probs.win_championship == {}
