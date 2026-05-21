"""Tests for per-game postseason importance derivation."""

import pytest

from src.scoring.importance import (
    POSTSEASON_MAX_SWING,
    normalize_postseason_importance,
)
from src.scoring.monte_carlo import compute_postseason_swing_from_matrix


def test_postseason_max_swing_is_two():
    """Ceiling = 2.0 (theoretical max: Σ|ΔP(champ)| when one game cleanly flips two teams)."""
    assert POSTSEASON_MAX_SWING == 2.0


def test_normalize_postseason_importance_zero_swing():
    assert normalize_postseason_importance(0.0) == 0.0


def test_normalize_postseason_importance_midpoint():
    # swing of 1.0 (half the max) → 50.0
    assert normalize_postseason_importance(1.0) == 50.0


def test_normalize_postseason_importance_caps_at_hundred():
    # swing of 2.0 → 100.0; anything beyond also caps at 100.0
    assert normalize_postseason_importance(2.0) == 100.0
    assert normalize_postseason_importance(3.5) == 100.0


def test_normalize_postseason_importance_floor_at_zero():
    # Defensive: negative values (shouldn't occur but be safe) → 0.0
    assert normalize_postseason_importance(-0.1) == 0.0


def test_postseason_swing_zero_when_higher_bucket_empty():
    """All sims have lower winning the focal game → swing = 0."""
    bracket_outcomes = [{("qf1", 1): False} for _ in range(100)]
    champions = ["T2"] * 100
    team_names = ["T1", "T2"]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, champions, team_names
    )
    assert swing == 0.0


def test_postseason_swing_zero_when_lower_bucket_empty():
    bracket_outcomes = [{("qf1", 1): True} for _ in range(100)]
    champions = ["T1"] * 100
    team_names = ["T1", "T2"]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, champions, team_names
    )
    assert swing == 0.0


def test_postseason_swing_skips_sims_missing_focal_game():
    """Sims where the focal game wasn't played are excluded from both buckets."""
    bracket_outcomes = [
        # 50 sims where higher won game 1 and is champion
        *({("qf1", 1): True} for _ in range(50)),
        # 50 sims where lower won game 1 and is champion
        *({("qf1", 1): False} for _ in range(50)),
        # 50 sims where this game wasn't reached (no entry) → excluded
        *({} for _ in range(50)),
    ]
    champions = (
        ["T1"] * 50  # higher always champ when higher wins
        + ["T2"] * 50  # lower always champ when lower wins
        + ["T3"] * 50  # ignored
    )
    team_names = ["T1", "T2", "T3"]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, champions, team_names
    )
    # Higher bucket: T1 champ 100%, T2/T3 0%. Lower bucket: T2 champ 100%, T1/T3 0%.
    # Σ|Δ| = |1-0| + |0-1| + |0-0| = 2.0
    assert swing == pytest.approx(2.0, abs=1e-9)


def test_postseason_swing_two_when_game_cleanly_flips_champion():
    """A focal game that determines the champion outright produces swing ≈ 2.0."""
    bracket_outcomes = [
        *({("f", 7): True} for _ in range(500)),
        *({("f", 7): False} for _ in range(500)),
    ]
    champions = ["A"] * 500 + ["B"] * 500
    team_names = ["A", "B", "C"]
    swing = compute_postseason_swing_from_matrix(
        "f", 7, bracket_outcomes, champions, team_names
    )
    assert swing == pytest.approx(2.0, abs=1e-9)


def test_postseason_swing_low_when_focal_game_barely_moves_champion():
    """If both buckets have similar champion distributions, swing is small."""
    # 1000 sims, focal game outcome is independent of champion
    bracket_outcomes = []
    champions = []
    for i in range(1000):
        higher_won = i % 2 == 0
        bracket_outcomes.append({("qf1", 1): higher_won})
        # Champion is always T3 regardless of qf1 outcome.
        champions.append("T3")
    team_names = ["T1", "T2", "T3"]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, champions, team_names
    )
    # T3 has 100% in both buckets; T1, T2 have 0% in both → Σ|Δ| = 0.
    assert swing == pytest.approx(0.0, abs=1e-9)


def test_postseason_swing_ignores_none_champion_sims():
    """Sims where no champion was crowned (fewer than 8 seeded) don't crash."""
    bracket_outcomes = [{("qf1", 1): True}, {("qf1", 1): False}, {}]
    champions = ["T1", "T2", None]
    team_names = ["T1", "T2"]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, champions, team_names
    )
    # Higher bucket: 1 sim, T1 champ 100%. Lower bucket: 1 sim, T2 champ 100%.
    # Σ|Δ| = |1-0| + |0-1| = 2.0. Third sim excluded by missing key.
    assert swing == pytest.approx(2.0, abs=1e-9)


from scripts.daily_update import _find_bracket_slot


def test_find_bracket_slot_matches_pair():
    """Returns (slot_id, next_game_num) when teams match an in-progress slot."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {
        "qf1": SeriesState(
            higher="A", lower="B",
            higher_wins=1, lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
        "qf2": SeriesState(games_needed=_GAMES_NEEDED_BO3),
    }
    assert _find_bracket_slot(state, "A", "B") == ("qf1", 2)
    # Order of teams doesn't matter.
    assert _find_bracket_slot(state, "B", "A") == ("qf1", 2)


def test_find_bracket_slot_game_number_includes_pre_played():
    """Game number = higher_wins + lower_wins + 1."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO7

    state = {
        "f": SeriesState(
            higher="A", lower="B",
            higher_wins=3, lower_wins=3,
            games_needed=_GAMES_NEEDED_BO7,
        ),
    }
    assert _find_bracket_slot(state, "A", "B") == ("f", 7)


def test_find_bracket_slot_no_match_returns_none():
    """Teams not in the bracket → None."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {
        "qf1": SeriesState(
            higher="A", lower="B",
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    assert _find_bracket_slot(state, "X", "Y") is None


def test_find_bracket_slot_skips_decided_series():
    """A slot whose winner is set is treated as not matching (series is over)."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {
        "qf1": SeriesState(
            higher="A", lower="B",
            higher_wins=2, lower_wins=0,
            winner="A",
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    assert _find_bracket_slot(state, "A", "B") is None


def test_find_bracket_slot_skips_unseeded_slots():
    """A slot with higher/lower=None (downstream not yet resolved) doesn't match."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO5

    state = {
        "sf1": SeriesState(games_needed=_GAMES_NEEDED_BO5),
    }
    assert _find_bracket_slot(state, "A", "B") is None


from scripts.daily_update import _importance_for_game


def test_importance_for_game_postseason_fallback_when_no_bracket_state():
    """season_type=3 + no bracket_state → fallback 100.0."""
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    assert _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=None, bracket_outcomes=None, champions=None, team_names=None,
    ) == 100.0


def test_importance_for_game_postseason_fallback_when_teams_not_in_bracket():
    """season_type=3 + bracket_state without these teams → fallback 100.0."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {"qf1": SeriesState(higher="X", lower="Y", games_needed=_GAMES_NEEDED_BO3)}
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=state, bracket_outcomes=[], champions=[],
        team_names=["A", "B"],
    )
    assert result == 100.0


def test_importance_for_game_postseason_derives_from_swing():
    """When all data is present, returns normalized swing."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO7

    state = {
        "f": SeriesState(
            higher="A", lower="B",
            higher_wins=3, lower_wins=3,
            games_needed=_GAMES_NEEDED_BO7,
        ),
    }
    # Construct synthetic sims where Finals Game 7 cleanly flips the champion.
    bracket_outcomes = (
        [{("f", 7): True}] * 500
        + [{("f", 7): False}] * 500
    )
    champions = ["A"] * 500 + ["B"] * 500
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=state, bracket_outcomes=bracket_outcomes,
        champions=champions, team_names=["A", "B", "C"],
    )
    # Swing = 2.0 → normalized = 100.
    assert result == pytest.approx(100.0, abs=1e-6)


def test_importance_for_game_regular_season_unchanged():
    """Regular-season path still uses raw_swings + ceiling, ignoring new kwargs."""
    game = {"season_type": 2, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    raw_swings = [0.375]  # half the default ceiling
    remaining_event_index = {"evt-1": 0}
    result = _importance_for_game(
        game, raw_swings=raw_swings, remaining_event_index=remaining_event_index,
        importance_ceiling=0.75,
    )
    assert result == pytest.approx(50.0, abs=1e-6)


def test_importance_for_game_preseason_returns_zero():
    """Preseason (season_type=1) returns 0 regardless of new kwargs."""
    game = {"season_type": 1, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    assert _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
    ) == 0.0
