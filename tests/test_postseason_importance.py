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


def test_importance_for_game_postseason_partial_swing():
    """Swing of ~1.0 → normalized ~50.0; proves derivation path actually executes."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {
        "qf1": SeriesState(
            higher="A", lower="B",
            higher_wins=0, lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    # 500 sims higher won qf1 game 1 → A champ in 250, B in 0, C in 250
    # 500 sims lower won  qf1 game 1 → A champ in 0,   B in 250, C in 250
    # Higher bucket rates: A=0.5, B=0.0, C=0.5
    # Lower bucket rates:  A=0.0, B=0.5, C=0.5
    # Σ|Δ| = 0.5 + 0.5 + 0.0 = 1.0  → normalized = 50.0
    bracket_outcomes = []
    champions = []
    for _ in range(250):
        bracket_outcomes.append({("qf1", 1): True})
        champions.append("A")
    for _ in range(250):
        bracket_outcomes.append({("qf1", 1): True})
        champions.append("C")
    for _ in range(250):
        bracket_outcomes.append({("qf1", 1): False})
        champions.append("B")
    for _ in range(250):
        bracket_outcomes.append({("qf1", 1): False})
        champions.append("C")

    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=state, bracket_outcomes=bracket_outcomes,
        champions=champions, team_names=["A", "B", "C"],
    )
    assert result == pytest.approx(50.0, abs=1e-6)


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


def test_build_current_bracket_state_no_completed_postseason_yields_empty_bracket(
    monkeypatch,
):
    """When no postseason games have completed but seeding resolves 8 teams,
    return an empty bracket built from the seeding so opening-round Game 1
    can match a slot. Regression for the 'first playoff games always show
    importance=100' Codex finding."""
    from scripts.daily_update import _build_current_bracket_state

    monkeypatch.setattr(
        "scripts.daily_update.get_completed_postseason_games",
        lambda session, season_year: [],
    )

    standings = {
        n: {"wins": 30 - i, "losses": i, "bpi": 0.0, "elo": 1700 - i * 25, "h2h": {}}
        for i, n in enumerate(
            [
                "Las Vegas Aces",
                "New York Liberty",
                "Minnesota Lynx",
                "Indiana Fever",
                "Connecticut Sun",
                "Seattle Storm",
                "Atlanta Dream",
                "Chicago Sky",
                "Phoenix Mercury",
                "Dallas Wings",
                "Washington Mystics",
                "Los Angeles Sparks",
                "Golden State Valkyries",
                "Portland Fire",
                "Toronto Tempo",
            ]
        )
    }

    state = _build_current_bracket_state(session=None, standings=standings)
    assert state is not None
    # All 7 slots present, QFs populated, SF/F unseeded.
    assert set(state.keys()) == {"qf1", "qf2", "qf3", "qf4", "sf1", "sf2", "f"}
    for sid in ("qf1", "qf2", "qf3", "qf4"):
        assert state[sid].higher is not None
        assert state[sid].lower is not None
        assert state[sid].higher_wins == 0
        assert state[sid].lower_wins == 0
        assert state[sid].winner is None


def test_find_bracket_slot_matches_opening_round_game_one():
    """Game 1 of an opening-round QF is matchable via the empty bracket
    state. Without this, _importance_for_game falls back to flat 100."""
    from scripts.daily_update import _find_bracket_slot
    from src.scoring.playoffs import empty_bracket_state

    seeded = [
        "Las Vegas Aces",
        "New York Liberty",
        "Minnesota Lynx",
        "Indiana Fever",
        "Connecticut Sun",
        "Seattle Storm",
        "Atlanta Dream",
        "Chicago Sky",
    ]
    state = empty_bracket_state(seeded)
    # QF1 = #1 vs #8 → Aces vs Sky. Game 1 is the next game (0 + 0 + 1).
    assert _find_bracket_slot(state, "Las Vegas Aces", "Chicago Sky") == ("qf1", 1)
    # QF2 = #4 vs #5 → Fever vs Sun.
    assert _find_bracket_slot(state, "Indiana Fever", "Connecticut Sun") == ("qf2", 1)


def test_assign_postseason_slot_lookup_distinct_game_nums_in_same_series():
    """Three scheduled games of the same QF must map to game_num 1, 2, 3 —
    not all to game_num 1. Regression for the 'all upcoming games in a
    series get scored as the next game' Codex finding."""
    from scripts.daily_update import _assign_postseason_slot_lookup
    from src.scoring.playoffs import empty_bracket_state

    seeded = [
        "Las Vegas Aces", "New York Liberty", "Minnesota Lynx", "Indiana Fever",
        "Connecticut Sun", "Seattle Storm", "Atlanta Dream", "Chicago Sky",
    ]
    state = empty_bracket_state(seeded)
    upcoming = [
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-19", "event_id": "g1"},
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-21", "event_id": "g2"},
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-23", "event_id": "g3"},
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {
        "g1": ("qf1", 1),
        "g2": ("qf1", 2),
        "g3": ("qf1", 3),
    }


def test_assign_postseason_slot_lookup_respects_completed_games_in_slot():
    """If the series is 1-0, the next two scheduled games get game_num 2 and 3,
    not 1 and 2."""
    from scripts.daily_update import _assign_postseason_slot_lookup
    from src.scoring.playoffs import (
        SeriesState, _GAMES_NEEDED_BO3, empty_bracket_state,
    )

    seeded = [
        "Las Vegas Aces", "New York Liberty", "Minnesota Lynx", "Indiana Fever",
        "Connecticut Sun", "Seattle Storm", "Atlanta Dream", "Chicago Sky",
    ]
    state = empty_bracket_state(seeded)
    state["qf1"] = SeriesState(
        higher="Las Vegas Aces", lower="Chicago Sky",
        higher_wins=1, lower_wins=0, games_needed=_GAMES_NEEDED_BO3,
    )
    upcoming = [
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-21", "event_id": "g2"},
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-23", "event_id": "g3"},
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {"g2": ("qf1", 2), "g3": ("qf1", 3)}


def test_assign_postseason_slot_lookup_handles_multiple_slots_independently():
    """Games across different slots are numbered independently within each."""
    from scripts.daily_update import _assign_postseason_slot_lookup
    from src.scoring.playoffs import empty_bracket_state

    seeded = [
        "Las Vegas Aces", "New York Liberty", "Minnesota Lynx", "Indiana Fever",
        "Connecticut Sun", "Seattle Storm", "Atlanta Dream", "Chicago Sky",
    ]
    state = empty_bracket_state(seeded)
    upcoming = [
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-19", "event_id": "qf1g1"},
        {"season_type": 3, "team_a": "Indiana Fever", "team_b": "Connecticut Sun",
         "date": "2026-09-19", "event_id": "qf2g1"},
        {"season_type": 3, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-09-21", "event_id": "qf1g2"},
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {
        "qf1g1": ("qf1", 1),
        "qf1g2": ("qf1", 2),
        "qf2g1": ("qf2", 1),
    }


def test_assign_postseason_slot_lookup_skips_unmatchable_games():
    """Postseason rows for teams not in any slot are omitted, not
    silently slotted somewhere."""
    from scripts.daily_update import _assign_postseason_slot_lookup
    from src.scoring.playoffs import empty_bracket_state

    seeded = [
        "Las Vegas Aces", "New York Liberty", "Minnesota Lynx", "Indiana Fever",
        "Connecticut Sun", "Seattle Storm", "Atlanta Dream", "Chicago Sky",
    ]
    state = empty_bracket_state(seeded)
    upcoming = [
        # Mystics weren't seeded — can't be in any bracket slot.
        {"season_type": 3, "team_a": "Washington Mystics", "team_b": "Chicago Sky",
         "date": "2026-09-19", "event_id": "mystery"},
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {}


def test_assign_postseason_slot_lookup_ignores_regular_season_rows():
    """season_type != 3 games are ignored even if their team pair happens
    to match a bracket slot."""
    from scripts.daily_update import _assign_postseason_slot_lookup
    from src.scoring.playoffs import empty_bracket_state

    seeded = [
        "Las Vegas Aces", "New York Liberty", "Minnesota Lynx", "Indiana Fever",
        "Connecticut Sun", "Seattle Storm", "Atlanta Dream", "Chicago Sky",
    ]
    state = empty_bracket_state(seeded)
    upcoming = [
        {"season_type": 2, "team_a": "Las Vegas Aces", "team_b": "Chicago Sky",
         "date": "2026-08-01", "event_id": "reg"},
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {}


def test_importance_for_game_uses_postseason_slot_lookup_when_provided():
    """Same upcoming game, called twice with lookup pointing at different
    game numbers, must produce different importance — proves the ordinal
    actually flows into compute_postseason_swing_from_matrix."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {
        "qf1": SeriesState(
            higher="A", lower="B", higher_wins=0, lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    # Sim universe: Game 1 cleanly flips champion (swing=2.0 → 100).
    # Game 2 has no effect on champion (swing=0 → 0).
    bracket_outcomes = []
    champions = []
    for _ in range(500):
        bracket_outcomes.append({("qf1", 1): True, ("qf1", 2): True})
        champions.append("A")
    for _ in range(500):
        bracket_outcomes.append({("qf1", 1): False, ("qf1", 2): True})
        champions.append("B")

    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "g1"}

    # Lookup says this is Game 1 of qf1 → swing 2.0 → importance 100.
    result_game1 = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=state, bracket_outcomes=bracket_outcomes,
        champions=champions, team_names=["A", "B"],
        postseason_slot_lookup={"g1": ("qf1", 1)},
    )
    assert result_game1 == pytest.approx(100.0, abs=1e-6)

    # Same game dict, lookup overrides to Game 2 → all sims have Game 2 = True,
    # so the lower-bucket is empty → swing 0 → importance 0.
    result_game2 = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=state, bracket_outcomes=bracket_outcomes,
        champions=champions, team_names=["A", "B"],
        postseason_slot_lookup={"g1": ("qf1", 2)},
    )
    assert result_game2 == pytest.approx(0.0, abs=1e-6)


def test_importance_for_game_lookup_miss_falls_back_to_max():
    """When postseason_slot_lookup is provided but doesn't contain this
    game's event_id, return 100 (unmatched). Don't silently fall through
    to _find_bracket_slot, which would assign every unlisted upcoming
    game the same 'next game' number."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO3

    state = {
        "qf1": SeriesState(
            higher="A", lower="B", higher_wins=0, lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "unknown"}
    result = _importance_for_game(
        game, raw_swings=[], remaining_event_index={}, importance_ceiling=0.75,
        bracket_state=state, bracket_outcomes=[{("qf1", 1): True}],
        champions=["A"], team_names=["A", "B"],
        postseason_slot_lookup={"g1": ("qf1", 1)},
    )
    assert result == 100.0
