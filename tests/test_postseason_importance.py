"""Tests for per-game postseason importance derivation."""

import math
import random

import pytest

from scripts.daily_update import _find_bracket_slot, _importance_for_game
from src.scoring.importance import (
    POSTSEASON_MAX_SWING,
    normalize_postseason_importance,
)
from src.scoring.monte_carlo import (
    FATE_CHAMPION,
    FATE_LOST_FINALS,
    FATE_LOST_QF,
    FATE_LOST_SF,
    FATE_MISSED,
    compute_postseason_swing_from_matrix,
    run_monte_carlo_simulation,
    to_team_standings,
)
from src.scoring.playoffs import _GAMES_NEEDED_BO3, empty_bracket_state
from src.scoring.tiebreakers import resolve_seeding


def test_postseason_swing_reaches_four_for_win_or_go_home():
    """A win-or-go-home game moves each participant exactly 2 units of total
    variation — 1 out of its current level, 1 across the deeper levels it may
    reach — so the pair sums to the structural maximum of 4.0.

    The advancing team's fate is spread 55/25/20 across the deeper levels on
    purpose: a uniform split would still sum to 2.0 per team even if the
    implementation collapsed levels, so it could not detect that bug.
    """
    n = 500
    bracket_outcomes = [{("qf1", 3): True} for _ in range(n)] + [
        {("qf1", 3): False} for _ in range(n)
    ]

    def advanced(i):
        pos = i % 100
        if pos < 55:
            return FATE_LOST_SF
        if pos < 80:
            return FATE_LOST_FINALS
        return FATE_CHAMPION

    fate_levels = [{"A": advanced(i), "B": FATE_LOST_QF} for i in range(n)]
    fate_levels += [{"A": FATE_LOST_QF, "B": advanced(i)} for i in range(n)]

    swing = compute_postseason_swing_from_matrix(
        "qf1", 3, bracket_outcomes, fate_levels, ("A", "B")
    )

    # Raw = 4.0. Floor: per participant, one half-normal term per level at the
    # pooled rate. Pooled rates per team: lost_qf 0.5, lost_sf 0.275,
    # lost_finals 0.125, champion 0.10, missed 0 (contributes nothing).
    def term(p):
        return math.sqrt(2 / math.pi) * math.sqrt(p * (1 - p) * (1 / n + 1 / n))

    expected_floor = 2 * (term(0.5) + term(0.275) + term(0.125) + term(0.10))
    assert swing == pytest.approx(4.0 - expected_floor, abs=1e-9)
    # NB: the brief's original loose bound here was `3.9 < swing < 4.0`, which
    # contradicts its own expected_floor formula one line above (that formula
    # evaluates to a floor of ~0.1592, giving swing ~3.8408 -- not >3.9). The
    # precise pytest.approx assertion above is the real, load-bearing check;
    # this is just a sanity band around the same true value.
    assert 3.8 < swing < 4.0


def test_postseason_max_swing_is_four():
    """Structural ceiling: 2 units of total variation per participant x 2."""
    assert POSTSEASON_MAX_SWING == 4.0


def test_normalize_postseason_importance_zero_swing():
    assert normalize_postseason_importance(0.0) == 0.0


def test_normalize_postseason_importance_midpoint():
    # swing of 2.0 (half the max) → 50.0
    assert normalize_postseason_importance(2.0) == 50.0


def test_normalize_postseason_importance_caps_at_hundred():
    # swing of 4.0 → 100.0; anything beyond also caps at 100.0
    assert normalize_postseason_importance(4.0) == 100.0
    assert normalize_postseason_importance(5.5) == 100.0


def test_normalize_postseason_importance_floor_at_zero():
    # Defensive: negative values (shouldn't occur but be safe) → 0.0
    assert normalize_postseason_importance(-0.1) == 0.0


def test_postseason_swing_zero_when_higher_bucket_empty():
    """All sims have lower winning the focal game → swing = 0."""
    bracket_outcomes = [{("qf1", 1): False} for _ in range(100)]
    fate_levels = [{"T1": FATE_LOST_QF, "T2": FATE_CHAMPION} for _ in range(100)]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, fate_levels, ("T1", "T2")
    )
    assert swing == 0.0


def test_postseason_swing_zero_when_lower_bucket_empty():
    bracket_outcomes = [{("qf1", 1): True} for _ in range(100)]
    fate_levels = [{"T1": FATE_CHAMPION, "T2": FATE_LOST_QF} for _ in range(100)]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, fate_levels, ("T1", "T2")
    )
    assert swing == 0.0


def test_postseason_swing_skips_sims_missing_focal_game():
    """Sims where the focal game wasn't played are excluded from both buckets."""
    n = 50
    bracket_outcomes = [
        *({("qf1", 1): True} for _ in range(n)),
        *({("qf1", 1): False} for _ in range(n)),
        *({} for _ in range(n)),
    ]
    fate_levels = (
        [{"T1": FATE_CHAMPION, "T2": FATE_LOST_QF} for _ in range(n)]
        + [{"T1": FATE_LOST_QF, "T2": FATE_CHAMPION} for _ in range(n)]
        + [{"T1": FATE_LOST_SF, "T2": FATE_LOST_SF} for _ in range(n)]
    )
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, fate_levels, ("T1", "T2")
    )
    # Each participant moves fully between two levels -> 2 units each -> raw 4.0.
    # Two levels per participant carry a floor term at pooled p=0.5; the other
    # three have pooled rate 0 and contribute nothing. Excluded sims never enter.
    expected_floor = 4 * math.sqrt(2 / math.pi) * math.sqrt(0.25 * (1 / n + 1 / n))
    assert swing == pytest.approx(4.0 - expected_floor, abs=1e-9)


def test_postseason_swing_four_when_game_decides_both_seasons():
    """Finals Game 7: the winner is champion, the loser is a finals loser.
    Each team moves 1 unit on each of two levels -> raw 4.0."""
    n = 500
    bracket_outcomes = [
        *({("f", 7): True} for _ in range(n)),
        *({("f", 7): False} for _ in range(n)),
    ]
    fate_levels = [{"A": FATE_CHAMPION, "B": FATE_LOST_FINALS} for _ in range(n)]
    fate_levels += [{"A": FATE_LOST_FINALS, "B": FATE_CHAMPION} for _ in range(n)]

    swing = compute_postseason_swing_from_matrix(
        "f", 7, bracket_outcomes, fate_levels, ("A", "B")
    )
    # Two levels per team carry a floor term at pooled p=0.5; the other three
    # have pooled rate 0 and contribute nothing. 2 teams x 2 levels = 4 terms.
    expected_floor = 4 * math.sqrt(2 / math.pi) * math.sqrt(0.25 * (1 / n + 1 / n))
    assert swing == pytest.approx(4.0 - expected_floor, abs=1e-9)


def test_postseason_swing_ignores_sims_with_no_bracket():
    """Sims where fewer than 8 teams seeded have no fate entry -> no contribution."""
    bracket_outcomes = [{("qf1", 1): True}, {("qf1", 1): False}, {("qf1", 1): True}]
    fate_levels = [
        {"A": FATE_CHAMPION, "B": FATE_LOST_QF},
        {"A": FATE_LOST_QF, "B": FATE_CHAMPION},
        {},  # no bracket played: neither team has a fate
    ]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, fate_levels, ("A", "B")
    )

    # Higher bucket has 2 sims but only 1 carries fates, so rates are halved
    # against n_h=2: A's champion rate is 0.5 (n_h) vs 0.0 (n_l=1) -> |0.5-0|;
    # A's lost_qf rate is 0.0 vs 1.0 -> |0-1|; same shape for B with champion
    # and lost_qf swapped. Raw = 3.0 (0.5+1.0 for each of A and B). Floor:
    # only lost_qf's pooled rate is nonzero across both participants (A's
    # champion pools with B's champion at rate 1/3 too) -- pooled rate is
    # 1/3 for both the lost_qf level and the champion level, n_a=2, n_b=1,
    # 4 such terms (2 participants x 2 nonzero levels).
    def term(p, n_a, n_b):
        variance = p * (1 - p) * (1 / n_a + 1 / n_b)
        return math.sqrt(2 / math.pi * variance)

    expected = 3.0 - 4 * term(1 / 3, 2, 1)
    assert swing == pytest.approx(expected, abs=1e-9)


def test_postseason_swing_ignores_non_participant_teams():
    """A team not in `participants` must not contribute to the swing, even
    when its fate correlates perfectly with the focal game's outcome --
    that's bracket bookkeeping (which opponent a non-participant draws
    next), not fate, per compute_postseason_swing_from_matrix's docstring
    ("Participants-only, unlike the regular season's all-teams sum").

    Same shape as test_postseason_swing_four_when_game_decides_both_seasons
    (Finals Game 7, A/B), with a third team C added whose fate is CHAMPION
    whenever A wins and MISSED whenever A loses -- a stronger correlation
    with the focal outcome than either participant's own fate swing. If the
    function summed over every team in `fate_levels` instead of only
    `participants`, C's perfectly-correlated champion/missed swing would
    inflate this above the exact two-participant value asserted below.
    """
    n = 500
    bracket_outcomes = [
        *({("f", 7): True} for _ in range(n)),
        *({("f", 7): False} for _ in range(n)),
    ]
    fate_levels = [
        {"A": FATE_CHAMPION, "B": FATE_LOST_FINALS, "C": FATE_CHAMPION}
        for _ in range(n)
    ] + [
        {"A": FATE_LOST_FINALS, "B": FATE_CHAMPION, "C": FATE_MISSED} for _ in range(n)
    ]

    swing = compute_postseason_swing_from_matrix(
        "f", 7, bracket_outcomes, fate_levels, ("A", "B")
    )
    # Identical to test_postseason_swing_four_when_game_decides_both_seasons:
    # two levels per participant (A, B) carry a floor term at pooled p=0.5;
    # C is excluded entirely by the participants-only restriction, so its
    # champion/missed swing appears in neither the raw sum nor the floor.
    expected_floor = 4 * math.sqrt(2 / math.pi) * math.sqrt(0.25 * (1 / n + 1 / n))
    assert swing == pytest.approx(4.0 - expected_floor, abs=1e-9)


def test_postseason_swing_over_real_monte_carlo_output():
    """Integration test: feeds run_monte_carlo_simulation's REAL
    bracket_outcomes/fate_levels into compute_postseason_swing_from_matrix,
    rather than the hand-built dicts every other test in this file uses.

    Every other postseason test constructs bracket_outcomes/fate_levels by
    hand, so the seam between the simulator's actual per-sim output and this
    scoring function is otherwise unguarded -- a change to
    _fate_levels_for_sim, the bracket recorder's game-numbering, or
    _partition_bracket's key shape could leave every other test in this file
    green while silently zeroing or maxing out live playoff importance
    scores. This is the one path with no production track record (the site
    has never run compute_postseason_swing_from_matrix against a real
    playoff bracket in prod).

    RNG is seeded for reproducibility; num_simulations is kept low (500) to
    stay fast. remaining_games is empty and every team has a distinct win
    total, so seeding is fully deterministic across sims -- the only
    randomness is in how the bracket itself plays out.
    """
    teams = [
        "New York Liberty",
        "Las Vegas Aces",
        "Minnesota Lynx",
        "Connecticut Sun",
        "Indiana Fever",
        "Seattle Storm",
        "Atlanta Dream",
        "Phoenix Mercury",
    ]
    standings = {
        name: {"wins": 20 - i, "losses": i, "elo": 1600 - i * 10}
        for i, name in enumerate(teams)
    }
    seeded = resolve_seeding(to_team_standings(standings))
    assert seeded == teams  # strictly decreasing wins -> deterministic, no ties

    bracket_state = empty_bracket_state(seeded)
    # qf1 (1 seed vs 8 seed): resume at 1-1, so the next game (game 3) is
    # win-or-go-home.
    bracket_state["qf1"].higher_wins = 1
    bracket_state["qf1"].lower_wins = 1
    # qf2 (4 seed vs 5 seed): already decided -- the series is over, so
    # simulate_playoffs plays no more games in this slot and nothing gets
    # recorded in bracket_outcomes for it in any sim.
    bracket_state["qf2"].winner = bracket_state["qf2"].higher
    bracket_state["qf2"].higher_wins = _GAMES_NEEDED_BO3

    random.seed(20260830)
    _, _, _, bracket_outcomes, _, fate_levels = run_monte_carlo_simulation(
        standings,
        [],
        num_simulations=500,
        return_matrix=True,
        bracket_state=bracket_state,
    )

    win_or_go_home_swing = compute_postseason_swing_from_matrix(
        "qf1",
        3,
        bracket_outcomes,
        fate_levels,
        (bracket_state["qf1"].higher, bracket_state["qf1"].lower),
    )
    assert normalize_postseason_importance(win_or_go_home_swing) > 90

    decided_swing = compute_postseason_swing_from_matrix(
        "qf2",
        3,
        bracket_outcomes,
        fate_levels,
        (bracket_state["qf2"].higher, bracket_state["qf2"].lower),
    )
    assert normalize_postseason_importance(decided_swing) < 10


def test_find_bracket_slot_matches_pair():
    """Returns (slot_id, next_game_num) when teams match an in-progress slot."""
    from src.scoring.playoffs import SeriesState

    state = {
        "qf1": SeriesState(
            higher="A",
            lower="B",
            higher_wins=1,
            lower_wins=0,
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
            higher="A",
            lower="B",
            higher_wins=3,
            lower_wins=3,
            games_needed=_GAMES_NEEDED_BO7,
        ),
    }
    assert _find_bracket_slot(state, "A", "B") == ("f", 7)


def test_find_bracket_slot_no_match_returns_none():
    """Teams not in the bracket → None."""
    from src.scoring.playoffs import SeriesState

    state = {
        "qf1": SeriesState(
            higher="A",
            lower="B",
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    assert _find_bracket_slot(state, "X", "Y") is None


def test_find_bracket_slot_skips_decided_series():
    """A slot whose winner is set is treated as not matching (series is over)."""
    from src.scoring.playoffs import SeriesState

    state = {
        "qf1": SeriesState(
            higher="A",
            lower="B",
            higher_wins=2,
            lower_wins=0,
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


def test_importance_for_game_postseason_fallback_when_no_bracket_state():
    """season_type=3 + no bracket_state → fallback 100.0."""
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    assert (
        _importance_for_game(
            game,
            raw_swings=[],
            remaining_event_index={},
            importance_ceiling=0.75,
            bracket_state=None,
            bracket_outcomes=None,
            fate_levels=None,
            team_names=None,
        )
        == 100.0
    )


def test_importance_for_game_postseason_fallback_when_teams_not_in_bracket():
    """season_type=3 + bracket_state without these teams → fallback 100.0."""
    from src.scoring.playoffs import SeriesState

    state = {"qf1": SeriesState(higher="X", lower="Y", games_needed=_GAMES_NEEDED_BO3)}
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    result = _importance_for_game(
        game,
        raw_swings=[],
        remaining_event_index={},
        importance_ceiling=0.75,
        bracket_state=state,
        bracket_outcomes=[],
        fate_levels=[],
        team_names=["A", "B"],
    )
    assert result == 100.0


def test_importance_for_game_postseason_derives_from_swing():
    """When all data is present, returns normalized swing (with noise-floor correction)."""
    from src.scoring.playoffs import SeriesState, _GAMES_NEEDED_BO7

    state = {
        "f": SeriesState(
            higher="A",
            lower="B",
            higher_wins=3,
            lower_wins=3,
            games_needed=_GAMES_NEEDED_BO7,
        ),
    }
    # Construct synthetic sims where Finals Game 7 cleanly decides both fates.
    n = 500
    bracket_outcomes = [{("f", 7): True}] * n + [{("f", 7): False}] * n
    fate_levels = [{"A": FATE_CHAMPION, "B": FATE_LOST_FINALS} for _ in range(n)]
    fate_levels += [{"A": FATE_LOST_FINALS, "B": FATE_CHAMPION} for _ in range(n)]
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    result = _importance_for_game(
        game,
        raw_swings=[],
        remaining_event_index={},
        importance_ceiling=0.75,
        bracket_state=state,
        bracket_outcomes=bracket_outcomes,
        fate_levels=fate_levels,
        team_names=["A", "B", "C"],
    )
    # Two levels per team carry a floor term at pooled p=0.5; raw swing 4.0.
    expected_floor = 4 * math.sqrt(2 / math.pi) * math.sqrt(0.25 * (1 / n + 1 / n))
    expected_swing = 4.0 - expected_floor
    expected_importance = expected_swing / 4.0 * 100.0
    assert result == pytest.approx(expected_importance, abs=1e-6)


def test_importance_for_game_postseason_partial_swing():
    """Partial fate spread for the advancing team -> partial swing; proves
    the derivation path actually executes with a non-trivial split."""
    from src.scoring.playoffs import SeriesState

    state = {
        "qf1": SeriesState(
            higher="A",
            lower="B",
            higher_wins=0,
            lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    # 500 sims higher (A) won qf1 game 1: A's fate splits lost_sf/champion 50/50,
    # B is lost_qf.
    # 500 sims lower (B) won qf1 game 1: B's fate splits lost_sf/champion 50/50,
    # A is lost_qf.
    bracket_outcomes = []
    fate_levels = []
    for i in range(500):
        bracket_outcomes.append({("qf1", 1): True})
        fate_levels.append(
            {
                "A": FATE_LOST_SF if i % 2 == 0 else FATE_CHAMPION,
                "B": FATE_LOST_QF,
            }
        )
    for i in range(500):
        bracket_outcomes.append({("qf1", 1): False})
        fate_levels.append(
            {
                "A": FATE_LOST_QF,
                "B": FATE_LOST_SF if i % 2 == 0 else FATE_CHAMPION,
            }
        )

    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    result = _importance_for_game(
        game,
        raw_swings=[],
        remaining_event_index={},
        importance_ceiling=0.75,
        bracket_state=state,
        bracket_outcomes=bracket_outcomes,
        fate_levels=fate_levels,
        team_names=["A", "B", "C"],
    )
    # Per team: lost_qf pooled p=0.5 (500 vs 0 in one bucket / 0 vs 500 in the
    # other -> wait, pooled over both buckets = 500/1000=0.5), lost_sf pooled
    # p=0.25, champion pooled p=0.25. Raw swing per team = |1-0| (lost_qf) +
    # |0-0.5| (lost_sf) + |0-0.5| (champion) = 2.0; two teams -> raw 4.0.
    n = 500

    def term(p):
        return math.sqrt(2 / math.pi) * math.sqrt(p * (1 - p) * (1 / n + 1 / n))

    expected_floor = 2 * (term(0.5) + term(0.25) + term(0.25))
    expected_swing = 4.0 - expected_floor
    expected_importance = expected_swing / 4.0 * 100.0
    assert result == pytest.approx(expected_importance, abs=1e-6)


def test_importance_for_game_regular_season_unchanged():
    """Regular-season path still uses raw_swings + ceiling, ignoring new kwargs."""
    game = {"season_type": 2, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    raw_swings = [0.375]  # half the default ceiling
    remaining_event_index = {"evt-1": 0}
    result = _importance_for_game(
        game,
        raw_swings=raw_swings,
        remaining_event_index=remaining_event_index,
        importance_ceiling=0.75,
    )
    assert result == pytest.approx(50.0, abs=1e-6)


def test_importance_for_game_preseason_returns_zero():
    """Preseason (season_type=1) returns 0 regardless of new kwargs."""
    game = {"season_type": 1, "team_a": "A", "team_b": "B", "event_id": "evt-1"}
    assert (
        _importance_for_game(
            game,
            raw_swings=[],
            remaining_event_index={},
            importance_ceiling=0.75,
        )
        == 0.0
    )


def test_impute_missing_importance_uses_mean_of_computed():
    """A None importance imputes the mean of the games that were simulated,
    not 0 — so an unsimulated game blends in at typical stakes."""
    from scripts.daily_update import _impute_missing_importance

    assert _impute_missing_importance([60.0, None, 40.0]) == pytest.approx(50.0)


def test_impute_missing_importance_ignores_nones_in_mean():
    from scripts.daily_update import _impute_missing_importance

    assert _impute_missing_importance([80.0, None, None, 20.0]) == pytest.approx(50.0)


def test_impute_missing_importance_falls_back_to_zero_when_all_missing():
    """Degenerate case: nothing simulated today → 0.0 (ranking is quality-order
    regardless, since the same constant is added to every game)."""
    from scripts.daily_update import _impute_missing_importance

    assert _impute_missing_importance([None, None]) == 0.0
    assert _impute_missing_importance([]) == 0.0


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
    upcoming = [
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-19",
            "event_id": "g1",
        },
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-21",
            "event_id": "g2",
        },
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-23",
            "event_id": "g3",
        },
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
        SeriesState,
    )

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
    state["qf1"] = SeriesState(
        higher="Las Vegas Aces",
        lower="Chicago Sky",
        higher_wins=1,
        lower_wins=0,
        games_needed=_GAMES_NEEDED_BO3,
    )
    upcoming = [
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-21",
            "event_id": "g2",
        },
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-23",
            "event_id": "g3",
        },
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {"g2": ("qf1", 2), "g3": ("qf1", 3)}


def test_assign_postseason_slot_lookup_handles_multiple_slots_independently():
    """Games across different slots are numbered independently within each."""
    from scripts.daily_update import _assign_postseason_slot_lookup

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
    upcoming = [
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-19",
            "event_id": "qf1g1",
        },
        {
            "season_type": 3,
            "team_a": "Indiana Fever",
            "team_b": "Connecticut Sun",
            "date": "2026-09-19",
            "event_id": "qf2g1",
        },
        {
            "season_type": 3,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-09-21",
            "event_id": "qf1g2",
        },
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
    upcoming = [
        # Mystics weren't seeded — can't be in any bracket slot.
        {
            "season_type": 3,
            "team_a": "Washington Mystics",
            "team_b": "Chicago Sky",
            "date": "2026-09-19",
            "event_id": "mystery",
        },
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {}


def test_assign_postseason_slot_lookup_ignores_regular_season_rows():
    """season_type != 3 games are ignored even if their team pair happens
    to match a bracket slot."""
    from scripts.daily_update import _assign_postseason_slot_lookup

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
    upcoming = [
        {
            "season_type": 2,
            "team_a": "Las Vegas Aces",
            "team_b": "Chicago Sky",
            "date": "2026-08-01",
            "event_id": "reg",
        },
    ]
    lookup = _assign_postseason_slot_lookup(upcoming, state)
    assert lookup == {}


def test_importance_for_game_uses_postseason_slot_lookup_when_provided():
    """Same upcoming game, called twice with lookup pointing at different
    game numbers, must produce different importance — proves the ordinal
    actually flows into compute_postseason_swing_from_matrix."""
    from src.scoring.playoffs import SeriesState

    state = {
        "qf1": SeriesState(
            higher="A",
            lower="B",
            higher_wins=0,
            lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    # Sim universe: Game 1 cleanly decides both fates; Game 2 has no effect.
    bracket_outcomes = []
    fate_levels = []
    for _ in range(500):
        bracket_outcomes.append({("qf1", 1): True, ("qf1", 2): True})
        fate_levels.append({"A": FATE_CHAMPION, "B": FATE_LOST_QF})
    for _ in range(500):
        bracket_outcomes.append({("qf1", 1): False, ("qf1", 2): True})
        fate_levels.append({"A": FATE_LOST_QF, "B": FATE_CHAMPION})

    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "g1"}

    # Lookup says this is Game 1 of qf1 → decides both fates.
    result_game1 = _importance_for_game(
        game,
        raw_swings=[],
        remaining_event_index={},
        importance_ceiling=0.75,
        bracket_state=state,
        bracket_outcomes=bracket_outcomes,
        fate_levels=fate_levels,
        team_names=["A", "B"],
        postseason_slot_lookup={"g1": ("qf1", 1)},
    )
    # Each team moves 1 unit on each of two levels -> raw 4.0, with 4 floor
    # terms at pooled p=0.5. Normalized against POSTSEASON_MAX_SWING=4.0.
    expected_floor = 4 * math.sqrt(2 / math.pi) * math.sqrt(0.25 * (1 / 500 + 1 / 500))
    expected_swing = 4.0 - expected_floor
    expected_importance = expected_swing / 4.0 * 100.0
    assert result_game1 == pytest.approx(expected_importance, abs=1e-6)

    # Same game dict, lookup overrides to Game 2 → all sims have Game 2 = True,
    # so the lower-bucket is empty → swing 0 → importance 0.
    result_game2 = _importance_for_game(
        game,
        raw_swings=[],
        remaining_event_index={},
        importance_ceiling=0.75,
        bracket_state=state,
        bracket_outcomes=bracket_outcomes,
        fate_levels=fate_levels,
        team_names=["A", "B"],
        postseason_slot_lookup={"g1": ("qf1", 2)},
    )
    assert result_game2 == pytest.approx(0.0, abs=1e-6)


def test_importance_for_game_lookup_miss_falls_back_to_max():
    """When postseason_slot_lookup is provided but doesn't contain this
    game's event_id, return 100 (unmatched). Don't silently fall through
    to _find_bracket_slot, which would assign every unlisted upcoming
    game the same 'next game' number."""
    from src.scoring.playoffs import SeriesState

    state = {
        "qf1": SeriesState(
            higher="A",
            lower="B",
            higher_wins=0,
            lower_wins=0,
            games_needed=_GAMES_NEEDED_BO3,
        ),
    }
    game = {"season_type": 3, "team_a": "A", "team_b": "B", "event_id": "unknown"}
    result = _importance_for_game(
        game,
        raw_swings=[],
        remaining_event_index={},
        importance_ceiling=0.75,
        bracket_state=state,
        bracket_outcomes=[{("qf1", 1): True}],
        fate_levels=[{"A": FATE_CHAMPION, "B": FATE_LOST_QF}],
        team_names=["A", "B"],
        postseason_slot_lookup={"g1": ("qf1", 1)},
    )
    assert result == 100.0


def test_postseason_swing_clamps_at_zero_for_no_signal_game():
    """Identical fate distributions in both buckets -> corrected swing exactly 0."""
    bracket_outcomes = [{("qf1", 1): i % 2 == 0} for i in range(100)]
    # Each participant's fate is independent of the focal game's outcome:
    # raw swing 0, so subtracting the positive floor must clamp at 0.
    fate_levels = [
        {
            "A": FATE_CHAMPION if i % 4 in (0, 1) else FATE_LOST_QF,
            "B": FATE_LOST_QF if i % 4 in (0, 1) else FATE_CHAMPION,
        }
        for i in range(100)
    ]
    swing = compute_postseason_swing_from_matrix(
        "qf1", 1, bracket_outcomes, fate_levels, ("A", "B")
    )
    assert swing == 0.0
