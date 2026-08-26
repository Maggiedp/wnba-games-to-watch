import random

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

# Same 8-team fixture shape as test_bracket_outcomes_populated_when_eight_teams_seed
# in test_monte_carlo.py — real WNBA names required by
# assert_all_teams_have_conferences, exactly PLAYOFF_TEAMS (8) so every sim
# plays a full bracket and nobody misses the playoffs.
_EIGHT_TEAMS = [
    "Las Vegas Aces",
    "New York Liberty",
    "Minnesota Lynx",
    "Indiana Fever",
    "Connecticut Sun",
    "Seattle Storm",
    "Atlanta Dream",
    "Chicago Sky",
]
_EIGHT_STANDINGS = {
    name: {"wins": 30 - i, "losses": i, "elo": 1600 - i * 20, "h2h": {}}
    for i, name in enumerate(_EIGHT_TEAMS)
}

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


def test_fate_levels_are_consistent_with_the_real_bracket_for_eight_teams():
    """Integration-level check of the actual call site in
    run_monte_carlo_simulation (monte_carlo.py) that builds sim_fate from
    bracket["reached_semis"] / bracket["reached_finals"] — the direct
    _fate_levels_for_sim unit tests above pass hand-labeled sets and would
    not catch a transposed (reached_semis, reached_finals) argument order at
    that call site.

    With exactly PLAYOFF_TEAMS (8) teams seeded, every sim plays a full
    bracket and nobody misses the playoffs, so the fate-level counts are
    pinned: 1 champion, 1 finals loser (the runner-up), 2 semifinal losers,
    4 first-round (QF) losers, 0 missed. A transposed call would relabel
    both semifinal losers as lost_finals (since reached_finals ⊆
    reached_semis, the "elif name in reached_finals" branch — fed the real
    reached_semis set under transposition — still catches them), collapsing
    lost_sf to 0 and inflating lost_finals to 3. That miscount is exactly
    what this test would catch.
    """
    random.seed(0)
    _, _, _, bracket_outcomes, champions, fate_levels = run_monte_carlo_simulation(
        _EIGHT_STANDINGS, [], num_simulations=20, return_matrix=True
    )

    for i in range(20):
        sim_fate = fate_levels[i]
        assert set(sim_fate) == set(_EIGHT_TEAMS)

        # Sanity: this sim actually played a full bracket (guards against a
        # fixture regression making this test vacuous).
        slots = {sid for sid, _ in bracket_outcomes[i]}
        assert slots == {"qf1", "qf2", "qf3", "qf4", "sf1", "sf2", "f"}

        champs = [t for t, f in sim_fate.items() if f == FATE_CHAMPION]
        finals_losers = [t for t, f in sim_fate.items() if f == FATE_LOST_FINALS]
        sf_losers = [t for t, f in sim_fate.items() if f == FATE_LOST_SF]
        qf_losers = [t for t, f in sim_fate.items() if f == FATE_LOST_QF]
        missed = [t for t, f in sim_fate.items() if f == FATE_MISSED]

        assert champs == [champions[i]]
        assert len(finals_losers) == 1
        assert len(sf_losers) == 2
        assert len(qf_losers) == 4
        assert missed == []

        # A concrete semifinal loser must read lost_sf, not lost_finals.
        assert sf_losers[0] not in finals_losers
