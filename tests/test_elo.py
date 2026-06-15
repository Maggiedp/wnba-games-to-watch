"""Tests for the Elo rating engine."""

import math

from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    DEFAULT_SEASON_REGRESSION,
    EXPANSION_SEED_RATING,
    INITIAL_RATING,
    _mov_multiplier,
    expected_win_prob,
    replay_games,
    update_ratings,
)


def test_mov_multiplier_zero_or_negative_returns_one():
    assert _mov_multiplier(0, 100) == 1.0
    assert _mov_multiplier(-5, 100) == 1.0


def test_mov_multiplier_monotonic_in_margin():
    """At a fixed Elo gap, larger margins should yield larger multipliers."""
    assert _mov_multiplier(20, 0) > _mov_multiplier(5, 0)
    assert _mov_multiplier(40, 0) > _mov_multiplier(20, 0)


def test_mov_multiplier_damps_for_heavy_favorites():
    """Autocorrelation correction: same blowout buys less when the favorite was heavier."""
    blowout = 30
    big_favorite = _mov_multiplier(blowout, winner_elo_advantage=400)
    toss_up = _mov_multiplier(blowout, winner_elo_advantage=0)
    upset = _mov_multiplier(blowout, winner_elo_advantage=-200)
    assert big_favorite < toss_up < upset


def test_update_ratings_mov_amplifies_blowouts():
    """A 30-point win should move ratings more than a 1-point win, all else equal."""
    narrow_a, _ = update_ratings(1500, 1500, team_a_won=True, mov=1)
    blowout_a, _ = update_ratings(1500, 1500, team_a_won=True, mov=30)
    no_mov_a, _ = update_ratings(1500, 1500, team_a_won=True)
    # No-MOV behavior is unchanged when mov is omitted.
    assert math.isclose(no_mov_a - 1500, DEFAULT_K * 0.5)
    assert (blowout_a - 1500) > (narrow_a - 1500)


def test_update_ratings_mov_still_zero_sum():
    new_a, new_b = update_ratings(1500, 1500, team_a_won=True, mov=15)
    assert math.isclose(new_a + new_b, 3000.0)


def test_replay_use_mov_changes_ratings_vs_baseline():
    """Smoke test: enabling MOV produces different finals than the no-MOV path."""
    games = [
        {
            "date": "2025-05-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "1",
            "final_score_a": 100,
            "final_score_b": 70,
        },
        {
            "date": "2025-05-02",
            "team_a": "B",
            "team_b": "A",
            "winner_team": "B",
            "event_id": "2",
            "final_score_a": 95,
            "final_score_b": 90,
        },
    ]
    no_mov = replay_games(games, home_advantage=0.0, use_mov=False)
    with_mov = replay_games(games, home_advantage=0.0, use_mov=True)
    assert no_mov.final_ratings != with_mov.final_ratings


def test_replay_use_mov_handles_missing_scores():
    """Games without final scores fall back to multiplier=1.0 instead of crashing."""
    games = [
        {
            "date": "2025-05-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "1",
        },
    ]
    result = replay_games(games, home_advantage=0.0, use_mov=True)
    # Same as the no-MOV path since the multiplier defaults to 1.0.
    baseline = replay_games(games, home_advantage=0.0, use_mov=False)
    assert result.final_ratings == baseline.final_ratings


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
    assert result.final_ratings["A"] < EXPANSION_SEED_RATING
    assert result.final_ratings["B"] > EXPANSION_SEED_RATING


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
    assert result.history[1]["pre_a"] > EXPANSION_SEED_RATING
    assert result.history[1]["pre_b"] < EXPANSION_SEED_RATING


def test_k_factor_scales_update_magnitude():
    """Doubling K should double the rating movement from a given result."""
    a_small, _ = update_ratings(1500, 1500, team_a_won=True, k=10)
    a_large, _ = update_ratings(1500, 1500, team_a_won=True, k=20)
    assert math.isclose((a_large - 1500) / (a_small - 1500), 2.0)


def test_calibrated_defaults_pinned():
    """Pin the calibrated constants so a change is caught deliberately.

    Calibrated against 2024 (warm-up) + 2025 (311-game eval) WNBA results
    via scripts/validate_elo.py with MOV enabled. Grid-search optimum was
    (K=16, H=50). Without MOV the optimum was K~34 — MOV roughly halves
    the K needed because the multiplier carries part of the responsiveness.
    """
    assert DEFAULT_K == 16.0
    assert DEFAULT_HOME_ADVANTAGE == 50.0


def test_default_season_regression():
    """Pin the calibrated regression value so a change is caught deliberately."""
    assert math.isclose(DEFAULT_SEASON_REGRESSION, 0.5)


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


def _two_season_games():
    """Two 2024 wins for A, then one 2025 game where A and B's pre-game ratings are inspected."""
    return [
        {
            "date": "2024-06-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "1",
        },
        {
            "date": "2024-07-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "2",
        },
        {
            "date": "2025-05-15",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "3",
        },
    ]


def test_single_season_replay_unaffected_by_regression():
    """Within one season, regression must not fire — every game has the same year."""
    games = [
        {
            "date": "2025-05-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "1",
        },
        {
            "date": "2025-06-01",
            "team_a": "A",
            "team_b": "B",
            "winner_team": "A",
            "event_id": "2",
        },
    ]
    no_reg = replay_games(games, season_regression=0.0, home_advantage=0.0)
    with_reg = replay_games(games, season_regression=0.5, home_advantage=0.0)
    assert no_reg.final_ratings == with_reg.final_ratings


def test_season_boundary_regresses_ratings_toward_mean():
    """Crossing into a new year pulls accumulated ratings toward INITIAL_RATING."""
    games = _two_season_games()

    no_reg = replay_games(games, season_regression=0.0, home_advantage=0.0)
    with_reg = replay_games(games, season_regression=1.0 / 3.0, home_advantage=0.0)

    # A built a lead in 2024 with both implementations.
    assert no_reg.history[1]["pre_a"] > EXPANSION_SEED_RATING

    # With regression, A entered the 2025 game closer to 1500 than without.
    # A starts below 1500 so regression pushes up; no_reg stays lower.
    assert with_reg.history[2]["pre_a"] > no_reg.history[2]["pre_a"]
    assert abs(with_reg.history[2]["pre_a"] - INITIAL_RATING) < abs(
        no_reg.history[2]["pre_a"] - INITIAL_RATING
    )


def test_season_regression_factor_one_resets_to_initial():
    """Factor=1.0 wipes accumulated history at the season boundary."""
    games = _two_season_games()
    result = replay_games(games, season_regression=1.0, home_advantage=0.0)
    # The first 2025 game inspects pre-game ratings — those should be the post-reset values.
    assert math.isclose(result.history[2]["pre_a"], INITIAL_RATING)
    assert math.isclose(result.history[2]["pre_b"], INITIAL_RATING)


def _game(a, b, winner, date, sa=80, sb=70, eid="e"):
    return {
        "team_a": a,
        "team_b": b,
        "winner_team": winner,
        "date": date,
        "final_score_a": sa,
        "final_score_b": sb,
        "event_id": eid,
    }


def test_is_replayable_predicate():
    from src.scoring.elo import is_replayable

    assert is_replayable({"team_a": "A", "team_b": "B", "winner_team": "A"})
    assert not is_replayable({"team_a": "A", "team_b": "B", "winner_team": None})
    assert not is_replayable({"team_a": "A", "team_b": "B", "winner_team": ""})
    # truthy-but-invalid winner (e.g. ESPN name drift) is NOT replayable
    assert not is_replayable({"team_a": "A", "team_b": "B", "winner_team": "Zzz"})


def test_replay_records_home_adv_and_event_id_in_history():
    games = [
        _game(
            "Las Vegas Aces", "Seattle Storm", "Las Vegas Aces", "2026-05-20", eid="42"
        )
    ]
    replay = replay_games(games, home_advantage=50.0)
    assert replay.history[0]["home_adv"] == 50.0
    assert replay.history[0]["event_id"] == "42"


def test_replay_skips_truthy_invalid_winner():
    # A row with a truthy winner that is neither team must not be replayed.
    games = [
        _game(
            "Las Vegas Aces", "Seattle Storm", "Las Vegas Aces", "2026-05-20", eid="1"
        ),
        {
            "team_a": "Chicago Sky",
            "team_b": "Atlanta Dream",
            "winner_team": "Typo BC",  # neither team
            "date": "2026-05-21",
            "event_id": "2",
        },
    ]
    replay = replay_games(games, home_advantage=50.0)
    assert [h["event_id"] for h in replay.history] == ["1"]


def test_rest_travel_adjust_off_is_identical():
    games = [
        _game(
            "Las Vegas Aces", "Seattle Storm", "Las Vegas Aces", "2026-05-20", eid="1"
        ),
        _game(
            "Seattle Storm", "Las Vegas Aces", "Seattle Storm", "2026-05-21", eid="2"
        ),
    ]
    base = replay_games(games, home_advantage=50.0)
    same = replay_games(games, home_advantage=50.0, rest_travel_adjust=None)
    assert base.final_ratings == same.final_ratings


def test_rest_travel_adjust_changes_ratings():
    games = [
        _game("Las Vegas Aces", "Chicago Sky", "Las Vegas Aces", "2026-05-20", eid="1"),
        _game(
            "Seattle Storm", "Las Vegas Aces", "Seattle Storm", "2026-05-21", eid="2"
        ),
    ]

    # Net +200 Elo to the home team A whenever the away team B is on a back-to-back.
    def adjust(feat):
        return 200.0 if feat["b2b_b"] else 0.0

    base = replay_games(games, home_advantage=50.0)
    adj = replay_games(games, home_advantage=50.0, rest_travel_adjust=adjust)
    # Game 2: the Aces (team_b) are on a back-to-back, so the hook fires and the
    # post-game ratings diverge from baseline; game 2's effective advantage is 250.
    assert adj.final_ratings != base.final_ratings
    assert adj.history[1]["home_adv"] == 250.0  # 50 HCA + 200 adjustment


def test_rest_travel_hook_ignores_winnerless_rows():
    # A scheduled/winner-less row between two completed games must NOT advance a
    # team's rest/travel state — the hook should see the last COMPLETED game only.
    from src.scoring.rest_travel import ARENA_COORDS, haversine_miles

    seen = []

    def adjust(feat):
        seen.append(feat)
        return 0.0

    games = [
        _game("Las Vegas Aces", "Chicago Sky", "Las Vegas Aces", "2026-05-20", eid="1"),
        {  # scheduled / not yet played — no winner
            "team_a": "Seattle Storm",
            "team_b": "Las Vegas Aces",
            "winner_team": None,
            "date": "2026-05-22",
            "event_id": "2",
        },
        _game(
            "New York Liberty",
            "Las Vegas Aces",
            "New York Liberty",
            "2026-05-23",
            eid="3",
        ),
    ]
    replay_games(games, home_advantage=50.0, rest_travel_adjust=adjust)

    # Only the two decisive games are replayed -> two feature dicts.
    assert len(seen) == 2
    g3 = seen[1]
    # The Aces (team_b) come off their last COMPLETED game in Las Vegas on 05-20,
    # not the skipped Seattle row on 05-22:
    assert g3["rest_b"] == 2  # 05-20 -> 05-23 = 3 days between -> 2 rest (not 0/b2b)
    assert g3["b2b_b"] == 0
    expected_travel = haversine_miles(
        ARENA_COORDS["Las Vegas Aces"], ARENA_COORDS["New York Liberty"]
    )
    assert abs(g3["travel_b"] - expected_travel) < 1.0  # Vegas->NY, not Seattle->NY
