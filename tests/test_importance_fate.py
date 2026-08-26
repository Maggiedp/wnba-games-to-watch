from src.scoring.monte_carlo import (
    FATE_CHAMPION,
    FATE_LEVELS,
    FATE_LOST_FINALS,
    FATE_LOST_QF,
    FATE_LOST_SF,
    FATE_MISSED,
    _fate_levels_for_sim,
    run_monte_carlo_simulation,
)
from tests.test_monte_carlo import _G2, _S3

TEAMS = ["Champ", "RunnerUp", "SemiLoser", "QFLoser", "Missed"]


def test_fate_levels_map_each_team_to_how_far_it_got():
    fate = _fate_levels_for_sim(
        playoff_teams={"Champ", "RunnerUp", "SemiLoser", "QFLoser"},
        reached_semis={"Champ", "RunnerUp", "SemiLoser"},
        reached_finals={"Champ", "RunnerUp"},
        champion="Champ",
        team_names=TEAMS,
    )
    assert fate == {
        "Champ": FATE_CHAMPION,
        "RunnerUp": FATE_LOST_FINALS,
        "SemiLoser": FATE_LOST_SF,
        "QFLoser": FATE_LOST_QF,
        "Missed": FATE_MISSED,
    }


def test_fate_levels_are_the_five_declared_levels():
    assert FATE_LEVELS == (
        FATE_MISSED,
        FATE_LOST_QF,
        FATE_LOST_SF,
        FATE_LOST_FINALS,
        FATE_CHAMPION,
    )


def test_fate_levels_handle_a_sim_with_no_bracket_played():
    """Fewer than 8 seeded teams: no bracket, so qualifiers are lost_qf."""
    fate = _fate_levels_for_sim(
        playoff_teams={"QFLoser"},
        reached_semis=set(),
        reached_finals=set(),
        champion=None,
        team_names=TEAMS,
    )
    assert fate["QFLoser"] == FATE_LOST_QF
    assert fate["Champ"] == FATE_MISSED


def test_return_matrix_includes_one_fate_map_per_sim():
    result = run_monte_carlo_simulation(
        _S3, _G2, num_simulations=25, return_matrix=True
    )
    assert len(result) == 6
    fate_levels = result[5]
    assert len(fate_levels) == 25
    for sim_fate in fate_levels:
        assert set(sim_fate) == set(_S3)
        assert all(v in FATE_LEVELS for v in sim_fate.values())
