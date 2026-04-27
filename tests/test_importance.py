"""Tests for importance scoring."""

import random

from src.scoring.importance import compute_importance_score, normalize_importance_score
from src.scoring.monte_carlo import compute_importance_swing, run_monte_carlo_simulation


def _bubble_standings() -> dict[str, dict]:
    """13-team standings with a tight bubble race — teams 6-9 tied on the cutoff.

    Elo ratings are paired with BPI so either signal can drive win probability
    (Monte Carlo uses Elo; quality scoring still reads BPI).
    """
    return {
        "Team0": {"wins": 28, "losses": 7, "bpi": 5.0, "elo": 1650},
        "Team1": {"wins": 26, "losses": 9, "bpi": 4.0, "elo": 1620},
        "Team2": {"wins": 24, "losses": 11, "bpi": 3.0, "elo": 1590},
        "Team3": {"wins": 22, "losses": 13, "bpi": 2.0, "elo": 1560},
        "Team4": {"wins": 20, "losses": 15, "bpi": 1.0, "elo": 1530},
        "Team5": {"wins": 19, "losses": 16, "bpi": 0.5, "elo": 1515},
        "Team6": {"wins": 18, "losses": 17, "bpi": 0.0, "elo": 1500},
        "Team7": {"wins": 18, "losses": 17, "bpi": -0.5, "elo": 1485},
        "Team8": {"wins": 18, "losses": 17, "bpi": -1.0, "elo": 1470},
        "Team9": {"wins": 18, "losses": 17, "bpi": -1.5, "elo": 1455},
        "Team10": {"wins": 14, "losses": 21, "bpi": -3.0, "elo": 1410},
        "Team11": {"wins": 10, "losses": 25, "bpi": -5.0, "elo": 1350},
        "Team12": {"wins": 7, "losses": 28, "bpi": -6.0, "elo": 1320},
    }


_REMAINING = [
    ("Team6", "Team7"),  # bubble vs bubble — index 0
    ("Team8", "Team9"),
    ("Team0", "Team1"),
    ("Team2", "Team3"),
    ("Team4", "Team5"),
]


# --- normalize_importance_score ---


def test_normalize_zero():
    assert normalize_importance_score(0.0) == 0.0


def test_normalize_at_max():
    assert normalize_importance_score(0.75) == 100.0


def test_normalize_over_max_is_capped():
    assert normalize_importance_score(1.0) == 100.0
    assert normalize_importance_score(999.0) == 100.0


def test_normalize_midpoint():
    assert normalize_importance_score(0.375) == 50.0


# --- compute_importance_swing ---


def test_importance_swing_is_meaningful():
    """Bubble game swing should be large enough to matter — catches the old bug
    where compute_importance_swing returned Monte Carlo noise (~0.001) because
    it ran identical simulations instead of forcing each outcome."""
    random.seed(42)
    swing = compute_importance_swing(
        _bubble_standings(), _REMAINING, 0, num_simulations=2000
    )
    # All-team summation in a 4-way-tied bubble approaches the theoretical max ~2.0.
    assert swing > 1.0, f"Expected meaningful swing for bubble game, got {swing:.3f}"


def test_importance_swing_captures_bubble_watchers():
    """A game between a bubble team and a safely-locked-out team should still
    score well above zero — even though only one of the playing teams has a
    meaningful own-swing, the other bubble teams 'watching' shift in the
    standings and contribute to the all-team total."""
    standings = _bubble_standings()
    # Team6 is on the 4-way bubble (18-17); Team12 is safely locked out (7-28).
    # Team12's own swing is ~0; the only way this game scores high is if the
    # all-team sum picks up Team7/8/9's playoff-odds shifts. Give the watchers
    # several remaining games so their fate has variance in simulation.
    games = [
        ("Team6", "Team12"),  # target
        ("Team7", "Team0"),
        ("Team7", "Team5"),
        ("Team8", "Team1"),
        ("Team8", "Team4"),
        ("Team9", "Team2"),
        ("Team9", "Team3"),
        ("Team6", "Team1"),
        ("Team0", "Team1"),
    ]

    random.seed(42)
    swing = compute_importance_swing(standings, games, 0, num_simulations=2000)
    assert swing > 0.5, (
        f"Bubble-vs-locked-out should pick up watcher swing, got {swing:.3f}"
    )


def test_importance_swing_direction():
    """Winning should improve a team's playoff odds; losing should hurt."""
    random.seed(42)
    standings = _bubble_standings()
    games_without = _REMAINING[1:]  # remove the Team6 vs Team7 game

    standings_6_wins = {n: dict(d) for n, d in standings.items()}
    standings_6_wins["Team6"]["wins"] += 1
    standings_6_wins["Team7"]["losses"] += 1

    standings_7_wins = {n: dict(d) for n, d in standings.items()}
    standings_7_wins["Team7"]["wins"] += 1
    standings_7_wins["Team6"]["losses"] += 1

    probs_if_6_wins = run_monte_carlo_simulation(
        standings_6_wins, games_without, num_simulations=2000
    )
    probs_if_7_wins = run_monte_carlo_simulation(
        standings_7_wins, games_without, num_simulations=2000
    )

    assert probs_if_6_wins["Team6"] > probs_if_7_wins["Team6"]
    assert probs_if_7_wins["Team7"] > probs_if_6_wins["Team7"]


def test_importance_swing_out_of_bounds_returns_zero():
    assert compute_importance_swing(_bubble_standings(), _REMAINING, 99) == 0.0


def test_importance_swing_unknown_team_returns_zero():
    standings = {"TeamX": {"wins": 5, "losses": 5, "bpi": 0.0, "elo": 1500}}
    games = [("TeamX", "TeamUnknown")]
    assert compute_importance_swing(standings, games, 0) == 0.0


# --- compute_importance_score ---


def test_importance_score_in_bounds():
    random.seed(42)
    score = compute_importance_score(_bubble_standings(), _REMAINING, 0)
    assert 0.0 <= score <= 100.0


def test_importance_score_bubble_beats_safe():
    """Bubble game should score higher than a game between two safely-in teams."""
    random.seed(42)
    standings = _bubble_standings()
    bubble_score = compute_importance_score(standings, _REMAINING, 0)  # Team6 vs Team7
    safe_score = compute_importance_score(standings, _REMAINING, 2)  # Team0 vs Team1
    assert bubble_score > safe_score
