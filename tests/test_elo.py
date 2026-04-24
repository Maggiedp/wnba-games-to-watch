"""Tests for the Elo rating engine."""

import math

from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    INITIAL_RATING,
    expected_win_prob,
    replay_games,
    update_ratings,
)


def test_equal_ratings_give_50_50():
    assert expected_win_prob(1500, 1500) == 0.5


def test_win_prob_symmetry():
    """P(A beats B) + P(B beats A) = 1."""
    assert math.isclose(
        expected_win_prob(1600, 1400) + expected_win_prob(1400, 1600), 1.0
    )


def test_400_point_gap_is_roughly_91_percent():
    """Standard Elo property: 400-point gap ≈ 91% win probability."""
    assert math.isclose(expected_win_prob(1900, 1500), 10 / 11, abs_tol=1e-9)


def test_win_prob_monotonic():
    assert expected_win_prob(1700, 1500) > expected_win_prob(1600, 1500)
    assert expected_win_prob(1500, 1700) < expected_win_prob(1500, 1600)


def test_update_zero_sum():
    """Rating mass is conserved: whatever A gains, B loses."""
    new_a, new_b = update_ratings(1500, 1500, team_a_won=True)
    assert math.isclose(new_a + new_b, 3000.0)


def test_update_winner_gains_loser_loses():
    new_a, new_b = update_ratings(1500, 1500, team_a_won=True)
    assert new_a > 1500
    assert new_b < 1500


def test_upset_moves_ratings_more_than_expected_win():
    """Underdog winning should shift ratings by more than favorite winning."""
    fav_a, _ = update_ratings(1700, 1500, team_a_won=True)
    ups_a, _ = update_ratings(1700, 1500, team_a_won=False)

    fav_gain = fav_a - 1700
    upset_loss = 1700 - ups_a
    assert upset_loss > fav_gain


def test_equal_rating_update_is_half_k():
    """When equal, winner gains K/2 (since expected was 0.5)."""
    new_a, _ = update_ratings(1500, 1500, team_a_won=True, k=20)
    assert math.isclose(new_a, 1500 + 10.0)


def test_replay_empty_games_returns_empty_state():
    result = replay_games([])
    assert result.final_ratings == {}
    assert result.history == []


def test_replay_sorts_by_date():
    """Games provided out of order should still replay chronologically."""
    games = [
        {
            "date": "2025-06-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "B",
            "event_id": "2",
        },
        {
            "date": "2025-05-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "1",
        },
    ]
    result = replay_games(games)
    assert result.history[0]["date"] == "2025-05-01"
    assert result.history[1]["date"] == "2025-06-01"
    # A 1-1 B net; home bonus applied to team A both times. With H>0, A's win
    # was more expected and B's win was less expected, so net A drifts down.
    assert result.final_ratings["A"] < INITIAL_RATING
    assert result.final_ratings["B"] > INITIAL_RATING


def test_replay_skips_games_without_winner():
    games = [
        {"date": "2025-06-01", "team_a": "A", "team_b": "B", "winner_team": None},
        {"date": "2025-06-02", "team_a": "A", "team_b": "B", "winner_team": "A"},
    ]
    result = replay_games(games)
    assert len(result.history) == 1


def test_replay_records_pre_game_ratings():
    """History entries must capture ratings as they were BEFORE the game — needed for time-honest validation."""
    games = [
        {
            "date": "2025-05-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "1",
        },
        {
            "date": "2025-05-02",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "2",
        },
    ]
    result = replay_games(games)
    assert result.history[1]["pre_a"] > INITIAL_RATING
    assert result.history[1]["pre_b"] < INITIAL_RATING


def test_k_factor_scales_update_magnitude():
    """Doubling K should double the rating movement from a given result."""
    a_small, _ = update_ratings(1500, 1500, team_a_won=True, k=10)
    a_large, _ = update_ratings(1500, 1500, team_a_won=True, k=20)
    assert math.isclose((a_large - 1500) / (a_small - 1500), 2.0)


def test_calibrated_defaults_pinned():
    """Pin the calibrated constants so a change is caught deliberately.

    Calibrated against 2024 (warm-up) + 2025 (311-game eval) WNBA results
    via scripts/validate_elo.py. Grid-search optimum was (K=28, H=50).
    """
    assert DEFAULT_K == 28.0
    assert DEFAULT_HOME_ADVANTAGE == 50.0


def test_home_toss_up_predicts_calibrated_win_rate():
    """Home team with equal ratings should predict 57–62% win probability.

    Empirically the home team won 59.8% of evenly-rated 2025 games; H≈50
    maps that to ~57% which is inside the observed band.
    """
    p = expected_win_prob(1500, 1500, home_advantage=DEFAULT_HOME_ADVANTAGE)
    assert 0.57 < p < 0.62


def test_home_advantage_boosts_team_a():
    """Equal ratings with home bonus should favor team A."""
    neutral = expected_win_prob(1500, 1500)
    with_home = expected_win_prob(1500, 1500, home_advantage=70)
    assert neutral == 0.5
    assert with_home > 0.5


def test_home_advantage_exact_value():
    """H=70 on equal ratings matches the closed-form logistic value."""
    p = expected_win_prob(1500, 1500, home_advantage=70)
    assert math.isclose(p, 1 / (1 + 10 ** (-70 / 400)))


def test_home_advantage_negative_for_road():
    """Passing a negative value flips the bonus to team B — lets callers swap sides."""
    home_p = expected_win_prob(1500, 1500, home_advantage=70)
    road_p = expected_win_prob(1500, 1500, home_advantage=-70)
    assert math.isclose(home_p + road_p, 1.0)


def test_home_update_gives_home_favorite_less_credit():
    """A home favorite winning should move ratings less than the same win on the road."""
    home_a, _ = update_ratings(1500, 1500, team_a_won=True, home_advantage=70)
    road_a, _ = update_ratings(1500, 1500, team_a_won=True, home_advantage=0)
    assert (home_a - 1500) < (road_a - 1500)


def test_home_update_still_zero_sum():
    new_a, new_b = update_ratings(1500, 1500, team_a_won=True, home_advantage=70)
    assert math.isclose(new_a + new_b, 3000.0)
