from src.scoring.monte_carlo import (
    compute_directional_movers_from_matrix,
    compute_importance_from_matrix,
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
    # Sum of |if_a - if_b| over ALL teams (no top_n/min_delta) == existing swing.
    outcome_matrix = [[True], [True], [False], [False]]
    playoff_sets = [{"Sun"}, {"Sun", "Sky"}, {"Sky"}, set()]
    team_names = ["Sun", "Sky"]
    movers = compute_directional_movers_from_matrix(
        outcome_matrix, playoff_sets, 0, team_names, top_n=99, min_delta=0.0
    )
    directional_sum = sum(abs(m["if_a"] - m["if_b"]) for m in movers)
    expected = compute_importance_from_matrix(
        outcome_matrix, playoff_sets, [("Sun", "Sky")], team_names
    )[0]
    assert abs(directional_sum - expected) < 1e-9
