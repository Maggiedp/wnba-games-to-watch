"""Tests for importance scoring."""

import random

from src.scoring.importance import compute_importance_score, normalize_importance_score
from src.scoring.monte_carlo import compute_importance_swing, run_monte_carlo_simulation

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


def _bubble_standings_with_h2h() -> dict[str, dict]:
    """Same as _bubble_standings() but adds 2-2 H2H splits among the four bubble
    teams (_T[6-9]).  The even splits mean H2H alone can't resolve the 4-way tie,
    so the tiebreaker chain falls through to conference record and finally elo —
    preserving real variance across the two forced-outcome simulation branches.
    Used by tests that need realistic bubble dynamics with tiebreakers active."""
    standings = _bubble_standings()
    bubble = [_T[6], _T[7], _T[8], _T[9]]
    for name in bubble:
        standings[name] = dict(standings[name])
        standings[name]["h2h"] = {opp: [2, 2] for opp in bubble if opp != name}
    return standings


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
    # Use the H2H fixture so the 4-way tie resolves non-deterministically across
    # the two forced-outcome branches (2-2 splits leave the chain unsettled until elo).
    standings = _bubble_standings_with_h2h()
    # _T[6] (Atlanta Dream) is on the 4-way bubble (18-17); _T[12] (Phoenix Mercury)
    # is safely locked out (7-28). _T[12]'s own swing is ~0; the only way this game
    # scores high is if the all-team sum picks up _T[7]/8/9's playoff-odds shifts.
    # Give the watchers several remaining games so their fate has variance in sim.
    games = [
        (_T[6], _T[12]),  # target
        (_T[7], _T[0]),
        (_T[7], _T[5]),
        (_T[8], _T[1]),
        (_T[8], _T[4]),
        (_T[9], _T[2]),
        (_T[9], _T[3]),
        (_T[6], _T[1]),
        (_T[0], _T[1]),
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

    assert probs_if_6_wins[_T[6]] > probs_if_7_wins[_T[6]]
    assert probs_if_7_wins[_T[7]] > probs_if_6_wins[_T[7]]


def test_importance_swing_out_of_bounds_returns_zero():
    assert compute_importance_swing(_bubble_standings(), _REMAINING, 99) == 0.0


def test_importance_swing_unknown_team_returns_zero():
    standings = {"Las Vegas Aces": {"wins": 5, "losses": 5, "bpi": 0.0, "elo": 1500}}
    games = [("Las Vegas Aces", "TeamUnknown")]
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
    bubble_score = compute_importance_score(standings, _REMAINING, 0)  # _T[6] vs _T[7]
    safe_score = compute_importance_score(standings, _REMAINING, 2)  # _T[0] vs _T[1]
    assert bubble_score > safe_score
