import math
import random

from src.scoring.monte_carlo import (
    FATE_CHAMPION,
    FATE_LEVELS,
    FATE_LOST_FINALS,
    FATE_LOST_QF,
    FATE_LOST_SF,
    FATE_MISSED,
    _fate_levels_for_sim,
    compute_importance_from_matrix,
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


def _fate(level_by_team):
    return dict(level_by_team)


def test_locked_field_seeding_swing_is_not_zero():
    """THE DEFECT. All berths decided, but the game moves seeding.

    Every team makes the playoffs in every sim, so the old
    make-playoffs fate scores this exactly 0. The round-reached fate
    sees the seeding move.
    """
    teams = ["A", "B"]
    # 100 sims: A won the game in the first 50, B in the rest.
    outcome_matrix = [[True]] * 50 + [[False]] * 50
    fate_levels = [_fate({"A": FATE_LOST_SF, "B": FATE_LOST_QF})] * 50 + [
        _fate({"A": FATE_LOST_QF, "B": FATE_LOST_SF})
    ] * 50
    swings = compute_importance_from_matrix(
        outcome_matrix, fate_levels, [("A", "B")], teams
    )
    assert swings[0] > 0.5


def test_bubble_game_still_swings():
    """A berth genuinely in play must stay high (spec section 5, gate 1)."""
    teams = ["A", "B"]
    outcome_matrix = [[True]] * 50 + [[False]] * 50
    fate_levels = [_fate({"A": FATE_LOST_QF, "B": FATE_MISSED})] * 50 + [
        _fate({"A": FATE_MISSED, "B": FATE_LOST_QF})
    ] * 50
    swings = compute_importance_from_matrix(
        outcome_matrix, fate_levels, [("A", "B")], teams
    )
    assert swings[0] > 0.5


def test_decided_season_swings_zero():
    """Nothing moves: both outcomes leave every fate identical."""
    teams = ["A", "B"]
    outcome_matrix = [[True]] * 50 + [[False]] * 50
    fate_levels = [_fate({"A": FATE_CHAMPION, "B": FATE_MISSED})] * 100
    swings = compute_importance_from_matrix(
        outcome_matrix, fate_levels, [("A", "B")], teams
    )
    assert swings[0] == 0.0


def test_noise_floor_is_subtracted_per_level_not_per_team():
    """The floor is Σ over LEVELS of a half-normal term, not one term per team.

    Fixture: a single team "A" whose fate splits 80/20 between lost_qf and
    lost_sf depending on which side of the game outcome the sim falls on
    (n_a = n_b = 100). That's a genuinely positive raw swing:

        raw = |80/100 - 20/100| (lost_qf) + |20/100 - 80/100| (lost_sf) = 1.2

    The correct floor sums one half-normal term per level, using each
    level's own pooled rate (lost_qf and lost_sf are both pooled 0.5; the
    other three levels are pooled 0 and contribute 0):

        floor = 2 * sqrt(2/pi * 0.5*0.5*(1/100 + 1/100))
              = 2 * sqrt(2/pi * 0.005)

    A buggy version that accumulates ONE floor term per team (rather than
    per level) has no single well-defined pooled rate to use here — the
    per-team aggregate count for "A" is 100 on each side by construction
    (every sim assigns A exactly one level), so its pooled rate is always
    1.0 and the half-normal variance term collapses to 0. That version
    reports the floor as 0 and the corrected swing as the raw 1.2 — a
    different number from the correct ~1.0872, which is exactly what this
    test's exact-value assertion catches.

    The expected floor is inlined per the repo's anti-tautology convention
    (src/scoring/CLAUDE.md) — never call _noise_floor_term from a test that
    asserts its value.
    """
    teams = ["A"]
    outcome_matrix = [[True]] * 100 + [[False]] * 100
    fate_levels = (
        [_fate({"A": FATE_LOST_QF})] * 80
        + [_fate({"A": FATE_LOST_SF})] * 20
        + [_fate({"A": FATE_LOST_SF})] * 80
        + [_fate({"A": FATE_LOST_QF})] * 20
    )
    swings = compute_importance_from_matrix(
        outcome_matrix, fate_levels, [("A", "A")], teams
    )

    n_a = n_b = 100
    raw = abs(80 / n_a - 20 / n_b) + abs(20 / n_a - 80 / n_b)  # lost_qf + lost_sf

    floor = 0.0
    for pooled in (0.0, 0.5, 0.5, 0.0, 0.0):  # missed, qf, sf, finals, champ
        variance = pooled * (1.0 - pooled) * (1.0 / n_a + 1.0 / n_b)
        floor += math.sqrt(2.0 / math.pi * variance)

    expected = raw - floor
    assert expected > 1.0
    assert swings[0] == expected
