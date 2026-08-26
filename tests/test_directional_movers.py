import math

from src.scoring.monte_carlo import (
    FATE_CHAMPION,
    FATE_LOST_FINALS,
    FATE_LOST_QF,
    FATE_LOST_SF,
    FATE_MISSED,
    _noise_floor_term,
    compute_directional_movers_from_matrix,
    compute_importance_from_matrix,
    compute_postseason_movers_from_matrix,
    compute_postseason_swing_from_matrix,
)


def test_directional_movers_basic_split():
    # 4 sims, 1 game. team_a won in sims 0,1; team_b won in sims 2,3.
    outcome_matrix = [[True], [True], [False], [False]]
    # "Sun" makes playoffs (loses in the QF) only when team_a wins;
    # "Sky" only when team_b wins. "Locked" never makes it either way.
    fate_levels = [
        {"Sun": FATE_LOST_QF, "Sky": FATE_MISSED, "Locked": FATE_MISSED},
        {"Sun": FATE_LOST_QF, "Sky": FATE_MISSED, "Locked": FATE_MISSED},
        {"Sun": FATE_MISSED, "Sky": FATE_LOST_QF, "Locked": FATE_MISSED},
        {"Sun": FATE_MISSED, "Sky": FATE_LOST_QF, "Locked": FATE_MISSED},
    ]
    team_names = ["Sun", "Sky", "Locked"]

    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, game_idx=0, team_names=team_names
    )
    by_team = {m["team"]: m for m in movers}
    assert by_team["Sun"]["if_a"] == 1.0 and by_team["Sun"]["if_b"] == 0.0
    assert by_team["Sun"]["level"] == "playoffs"
    assert by_team["Sky"]["if_a"] == 0.0 and by_team["Sky"]["if_b"] == 1.0
    assert by_team["Sky"]["level"] == "playoffs"
    assert "Locked" not in by_team  # never makes playoffs -> delta 0 < min_delta


def test_directional_movers_empty_bucket_returns_empty():
    outcome_matrix = [[True], [True]]  # no team_b wins
    fate_levels = [{"Sun": FATE_LOST_QF}, {"Sun": FATE_LOST_QF}]
    assert (
        compute_directional_movers_from_matrix(outcome_matrix, fate_levels, 0, ["Sun"])
        == []
    )


def test_directional_movers_respects_top_n_and_min_delta():
    outcome_matrix = [[True], [False]]
    # A: 1.0/0.0 delta 1.0 ; B: 0.5/0.0 delta .5 ; tiny excluded by top_n
    fate_levels = [
        {"A": FATE_LOST_QF, "B": FATE_LOST_QF, "tiny": FATE_MISSED},
        {"A": FATE_MISSED, "B": FATE_MISSED, "tiny": FATE_MISSED},
    ]
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, ["A", "B", "tiny"], top_n=1
    )
    assert len(movers) == 1 and movers[0]["team"] == "A"


def test_reports_the_milestone_that_moved_most():
    """Berths locked, semis odds swing => the panel says 'semis'."""
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    fate_levels = [{"A": FATE_LOST_SF, "B": FATE_LOST_QF}] * 100 + [
        {"A": FATE_LOST_QF, "B": FATE_LOST_SF}
    ] * 100
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, ["A", "B"]
    )
    assert movers
    assert movers[0]["level"] == "semis"
    assert movers[0]["if_a"] == 1.0
    assert movers[0]["if_b"] == 0.0


def test_reports_finals_when_the_finals_odds_are_what_moved():
    """A semis berth is locked either way, but reaching the finals only
    happens under team_a -- the panel must say 'finals', not fall back to
    'semis' or jump to 'championship'. Discriminates the FATE_LOST_FINALS
    membership in _MILESTONES: if 'finals' omitted FATE_LOST_FINALS the
    delta would read 0 (no mover reported); if it also swept in
    FATE_LOST_SF the delta would likewise read 0 (both buckets 1.0)."""
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    fate_levels = [{"A": FATE_LOST_FINALS}] * 100 + [{"A": FATE_LOST_SF}] * 100
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, ["A"]
    )
    assert movers
    assert movers[0]["level"] == "finals"
    assert movers[0]["if_a"] == 1.0
    assert movers[0]["if_b"] == 0.0


def test_reports_championship_when_the_title_odds_are_what_moved():
    """A finals berth is locked either way, but winning the title only
    happens under team_a -- the panel must say 'championship', not fall
    back to 'finals'. Discriminates the {FATE_CHAMPION} membership in
    _MILESTONES: if it also swept in FATE_LOST_FINALS, the delta would
    read 0 (both buckets 1.0) and no mover would be reported."""
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    fate_levels = [{"A": FATE_CHAMPION}] * 100 + [{"A": FATE_LOST_FINALS}] * 100
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, ["A"]
    )
    assert movers
    assert movers[0]["level"] == "championship"
    assert movers[0]["if_a"] == 1.0
    assert movers[0]["if_b"] == 0.0


def test_bubble_game_still_reports_playoffs():
    """May behaviour is unchanged: the biggest mover is the berth."""
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    fate_levels = [{"A": FATE_LOST_QF, "B": FATE_MISSED}] * 100 + [
        {"A": FATE_MISSED, "B": FATE_LOST_QF}
    ] * 100
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, ["A", "B"]
    )
    assert movers[0]["level"] == "playoffs"


def test_movers_are_fractions_and_capped_at_top_n():
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    fate_levels = [{f"T{i}": FATE_CHAMPION for i in range(5)}] * 100 + [
        {f"T{i}": FATE_MISSED for i in range(5)}
    ] * 100
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, [f"T{i}" for i in range(5)], top_n=3
    )
    assert len(movers) == 3
    for m in movers:
        assert 0.0 <= m["if_a"] <= 1.0
        assert 0.0 <= m["if_b"] <= 1.0


def test_directional_sum_is_a_lower_bound_on_the_corrected_swing():
    """Superseded relationship (stretch-run-importance, Task 3): movers used
    to report one delta per team over a single binary flag (playoff_sets),
    so directional_sum == corrected_swing + floor exactly. Now movers report
    ONE milestone per team -- its single biggest mover -- while the swing
    sums over all FIVE exclusive fate levels per team. A team whose fate
    moves between two different exclusive levels (e.g. lost_qf under one
    outcome, lost_sf under the other) contributes to the swing at both
    levels but can only ever report one milestone's delta, so the movers'
    summed deltas are now strictly a LOWER bound on corrected_swing + floor
    (which recovers the pre-correction raw swing), not an equality.

    Uses n=100 per outcome bucket (not the 4-sim toy size elsewhere in this
    file) so the analytic noise floor stays small relative to the swing --
    at n=2 the floor can exceed the raw swing and clamp corrected_swing to
    0, which would make the comparison vacuous.
    """
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    # "Sun" loses in the QF in every sim where team_a wins, and loses in the
    # SF in every sim where team_b wins -- two different exclusive levels
    # move (lost_qf: 1.0 -> 0.0, lost_sf: 0.0 -> 1.0), but only "semis"
    # (lost_sf/lost_finals/champion) shows nonzero movement as a cumulative
    # milestone (semis: 0.0 -> 1.0); "playoffs" doesn't move at all (both
    # buckets make it 100% of the time, just via a different round).
    fate_levels = [{"Sun": FATE_LOST_QF}] * 100 + [{"Sun": FATE_LOST_SF}] * 100
    team_names = ["Sun"]

    movers = compute_directional_movers_from_matrix(
        outcome_matrix, fate_levels, 0, team_names, top_n=99, min_delta=0.0
    )
    assert len(movers) == 1
    assert movers[0]["level"] == "semis"
    directional_sum = sum(abs(m["if_a"] - m["if_b"]) for m in movers)
    assert directional_sum == 1.0

    corrected_swing = compute_importance_from_matrix(
        outcome_matrix, fate_levels, [("A", "B")], team_names
    )[0]

    # Inline the analytic noise-floor formula (repo's anti-tautology
    # convention forbids asserting a value by calling the function under
    # test to produce it) to reconstruct the raw, pre-correction swing as
    # corrected_swing + floor.
    a_indices = list(range(100))
    b_indices = list(range(100, 200))
    n_a, n_b = len(a_indices), len(b_indices)
    floor = 0.0
    for team in team_names:
        for level in (
            FATE_MISSED,
            FATE_LOST_QF,
            FATE_LOST_SF,
            FATE_LOST_FINALS,
            FATE_CHAMPION,
        ):
            count_a = sum(1 for s in a_indices if fate_levels[s].get(team) == level)
            count_b = sum(1 for s in b_indices if fate_levels[s].get(team) == level)
            pooled_rate = (count_a + count_b) / (n_a + n_b)
            variance = pooled_rate * (1.0 - pooled_rate) * (1.0 / n_a + 1.0 / n_b)
            floor += math.sqrt(2.0 / math.pi * variance)

    raw_swing = corrected_swing + floor
    assert directional_sum < raw_swing
    # Tightly bounded, not a vacuous "< some huge number": the reported
    # milestone (semis) misses the OTHER exclusive level (lost_qf) that
    # also moved by a full 1.0, so the gap between the raw swing and what
    # the panel reports is itself close to 1.0 -- not a rounding sliver.
    assert raw_swing - directional_sum > 0.5


def test_postseason_movers_basic_split():
    # 4 sims. Focal game ("f", 1): higher won in 0,1; lower won in 2,3.
    bracket_outcomes = [
        {("f", 1): True},
        {("f", 1): True},
        {("f", 1): False},
        {("f", 1): False},
    ]
    champions = ["Aces", "Aces", "Liberty", "Liberty"]
    movers = compute_postseason_movers_from_matrix(
        "f", 1, bracket_outcomes, champions, ["Aces", "Liberty"]
    )
    by_team = {m["team"]: m for m in movers}
    assert by_team["Aces"]["if_higher"] == 1.0 and by_team["Aces"]["if_lower"] == 0.0
    assert (
        by_team["Liberty"]["if_higher"] == 0.0 and by_team["Liberty"]["if_lower"] == 1.0
    )


def test_postseason_movers_empty_bucket():
    bracket_outcomes = [{("f", 1): True}, {("f", 1): True}]
    champions = ["Aces", "Aces"]
    assert (
        compute_postseason_movers_from_matrix(
            "f", 1, bracket_outcomes, champions, ["Aces"]
        )
        == []
    )


def test_postseason_sum_matches_existing_swing():
    # Movers report raw per-team deltas. The swing is floor-corrected.
    # Verify: directional_sum == corrected_swing + floor (within numerical precision).
    bracket_outcomes = [
        {("sf1", 2): True},
        {("sf1", 2): False},
        {("sf1", 2): True},
        {("sf1", 2): False},
    ]
    champions = ["Aces", "Liberty", "Aces", None]
    team_names = ["Aces", "Liberty"]
    movers = compute_postseason_movers_from_matrix(
        "sf1", 2, bracket_outcomes, champions, team_names, top_n=99, min_delta=0.0
    )
    directional_sum = sum(abs(m["if_higher"] - m["if_lower"]) for m in movers)
    corrected_swing = compute_postseason_swing_from_matrix(
        "sf1", 2, bracket_outcomes, champions, team_names
    )
    # Compute the analytic noise floor using the same logic as compute_postseason_swing_from_matrix
    higher_indices = [0, 2]  # sims where higher seed won
    lower_indices = [1, 3]  # sims where lower seed won
    n_h, n_l = len(higher_indices), len(lower_indices)
    floor = 0.0
    for team in team_names:
        count_h = sum(1 for i in higher_indices if champions[i] == team)
        count_l = sum(1 for i in lower_indices if champions[i] == team)
        pooled_rate = (count_h + count_l) / (n_h + n_l)
        floor += _noise_floor_term(pooled_rate, n_h, n_l)
    assert abs(directional_sum - (corrected_swing + floor)) < 1e-9
