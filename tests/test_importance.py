"""Tests for importance scoring."""

import random

from src.scoring.importance import normalize_importance_score
from src.scoring.monte_carlo import run_monte_carlo_simulation

# Real WNBA team names mapped to synthetic fixture roles (must be in TEAM_CONFERENCES).
# Ordered by strength: top 6 safely in, middle 4 on the bubble (tied 18-17),
# bottom 3 safely out.
_T = [
    "Las Vegas Aces",  # 0 — safely in, strongest
    "New York Liberty",  # 1 — safely in
    "Minnesota Lynx",  # 2 — safely in
    "Indiana Fever",  # 3 — safely in
    "Connecticut Sun",  # 4 — safely in
    "Seattle Storm",  # 5 — safely in, weakest safe
    "Atlanta Dream",  # 6 — bubble (18-17)
    "Chicago Sky",  # 7 — bubble (18-17)
    "Washington Mystics",  # 8 — bubble (18-17)
    "Dallas Wings",  # 9 — bubble (18-17)
    "Golden State Valkyries",  # 10 — safely out
    "Los Angeles Sparks",  # 11 — safely out
    "Phoenix Mercury",  # 12 — safely out, weakest
]


def _bubble_standings() -> dict[str, dict]:
    """13-team standings with a tight bubble race — teams 6-9 tied on the cutoff.

    Elo ratings are paired with BPI so either signal can drive win probability
    (Monte Carlo uses Elo; quality scoring still reads BPI).
    """
    return {
        _T[0]: {"wins": 28, "losses": 7, "bpi": 5.0, "elo": 1650},
        _T[1]: {"wins": 26, "losses": 9, "bpi": 4.0, "elo": 1620},
        _T[2]: {"wins": 24, "losses": 11, "bpi": 3.0, "elo": 1590},
        _T[3]: {"wins": 22, "losses": 13, "bpi": 2.0, "elo": 1560},
        _T[4]: {"wins": 20, "losses": 15, "bpi": 1.0, "elo": 1530},
        _T[5]: {"wins": 19, "losses": 16, "bpi": 0.5, "elo": 1515},
        _T[6]: {"wins": 18, "losses": 17, "bpi": 0.0, "elo": 1500},
        _T[7]: {"wins": 18, "losses": 17, "bpi": -0.5, "elo": 1485},
        _T[8]: {"wins": 18, "losses": 17, "bpi": -1.0, "elo": 1470},
        _T[9]: {"wins": 18, "losses": 17, "bpi": -1.5, "elo": 1455},
        _T[10]: {"wins": 14, "losses": 21, "bpi": -3.0, "elo": 1410},
        _T[11]: {"wins": 10, "losses": 25, "bpi": -5.0, "elo": 1350},
        _T[12]: {"wins": 7, "losses": 28, "bpi": -6.0, "elo": 1320},
    }


_REMAINING = [
    (_T[6], _T[7]),  # bubble vs bubble — index 0
    (_T[8], _T[9]),
    (_T[0], _T[1]),
    (_T[2], _T[3]),
    (_T[4], _T[5]),
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


# --- run_monte_carlo_simulation directionality ---


def test_make_playoffs_responds_to_forced_outcome():
    """Winning should improve a team's playoff odds; losing should hurt."""
    random.seed(42)
    standings = _bubble_standings()
    games_without = _REMAINING[1:]  # remove the _T[6] vs _T[7] game

    standings_6_wins = {n: dict(d) for n, d in standings.items()}
    standings_6_wins[_T[6]]["wins"] += 1
    standings_6_wins[_T[7]]["losses"] += 1

    standings_7_wins = {n: dict(d) for n, d in standings.items()}
    standings_7_wins[_T[7]]["wins"] += 1
    standings_7_wins[_T[6]]["losses"] += 1

    probs_if_6_wins = run_monte_carlo_simulation(
        standings_6_wins, games_without, num_simulations=2000
    )
    probs_if_7_wins = run_monte_carlo_simulation(
        standings_7_wins, games_without, num_simulations=2000
    )

    assert probs_if_6_wins.make_playoffs[_T[6]] > probs_if_7_wins.make_playoffs[_T[6]]
    assert probs_if_7_wins.make_playoffs[_T[7]] > probs_if_6_wins.make_playoffs[_T[7]]
