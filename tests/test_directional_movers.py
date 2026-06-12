from src.scoring.monte_carlo import (
    _noise_floor_term,
    compute_directional_movers_from_matrix,
    compute_importance_from_matrix,
    compute_postseason_movers_from_matrix,
    compute_postseason_swing_from_matrix,
)


def test_directional_movers_basic_split():
    # 4 sims, 1 game. team_a won in sims 0,1; team_b won in sims 2,3.
    outcome_matrix = [[True], [True], [False], [False]]
    # "Sun" makes playoffs only when team_a wins; "Sky" only when team_b wins.
    playoff_sets = [{"Sun"}, {"Sun"}, {"Sky"}, {"Sky"}]
    team_names = ["Sun", "Sky", "Locked"]

    movers = compute_directional_movers_from_matrix(
        outcome_matrix, playoff_sets, game_idx=0, team_names=team_names
    )
    by_team = {m["team"]: m for m in movers}
    assert by_team["Sun"]["if_a"] == 1.0 and by_team["Sun"]["if_b"] == 0.0
    assert by_team["Sky"]["if_a"] == 0.0 and by_team["Sky"]["if_b"] == 1.0
    assert "Locked" not in by_team  # never makes playoffs -> delta 0 < min_delta


def test_directional_movers_empty_bucket_returns_empty():
    outcome_matrix = [[True], [True]]  # no team_b wins
    playoff_sets = [{"Sun"}, {"Sun"}]
    assert (
        compute_directional_movers_from_matrix(outcome_matrix, playoff_sets, 0, ["Sun"])
        == []
    )


def test_directional_movers_respects_top_n_and_min_delta():
    outcome_matrix = [[True], [False]]
    # A: 1.0/0.0 delta 1.0 ; B: 0.5/0.0 delta .5 ; tiny excluded by top_n
    playoff_sets = [{"A", "B"}, set()]
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, playoff_sets, 0, ["A", "B", "tiny"], top_n=1
    )
    assert len(movers) == 1 and movers[0]["team"] == "A"


def test_directional_sum_matches_existing_swing():
    # Movers report raw per-team deltas. The swing is floor-corrected.
    # Verify: directional_sum == corrected_swing + floor (within numerical precision).
    outcome_matrix = [[True], [True], [False], [False]]
    playoff_sets = [{"Sun"}, {"Sun", "Sky"}, {"Sky"}, set()]
    team_names = ["Sun", "Sky"]
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, playoff_sets, 0, team_names, top_n=99, min_delta=0.0
    )
    directional_sum = sum(abs(m["if_a"] - m["if_b"]) for m in movers)
    corrected_swing = compute_importance_from_matrix(
        outcome_matrix, playoff_sets, [("Sun", "Sky")], team_names
    )[0]
    # Compute the analytic noise floor using the same logic as compute_importance_from_matrix
    a_indices = [0, 1]  # sims where team_a won
    b_indices = [2, 3]  # sims where team_b won
    n_a, n_b = len(a_indices), len(b_indices)
    floor = 0.0
    for team in team_names:
        count_a = sum(1 for s in a_indices if team in playoff_sets[s])
        count_b = sum(1 for s in b_indices if team in playoff_sets[s])
        pooled_rate = (count_a + count_b) / (n_a + n_b)
        floor += _noise_floor_term(pooled_rate, n_a, n_b)
    assert abs(directional_sum - (corrected_swing + floor)) < 1e-9


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
