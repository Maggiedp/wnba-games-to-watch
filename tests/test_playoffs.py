"""Tests for src/scoring/playoffs.py."""

import random

from src.scoring.monte_carlo import TeamStanding
from src.scoring.playoffs import (
    HOME_PATTERN_BO3,
    HOME_PATTERN_BO5,
    HOME_PATTERN_BO7,
    play_series,
    reconstruct_bracket_state,
    simulate_playoffs,
)


def _standings(elos: dict[str, float]) -> dict[str, TeamStanding]:
    return {n: TeamStanding(name=n, elo=e) for n, e in elos.items()}


def test_play_series_bo3_higher_seed_dominant_wins():
    """Higher seed with massive Elo edge should win the series ~always."""
    random.seed(1)
    s = _standings({"H": 1800, "L": 1200})
    wins = sum(play_series("H", "L", HOME_PATTERN_BO3, s) == "H" for _ in range(200))
    assert wins >= 190


def test_play_series_short_circuits_at_majority():
    """A Bo3 should never call random.random() more than 3 times."""
    s = _standings({"H": 1500, "L": 1500})

    call_count = {"n": 0}
    real_random = random.random

    def counting_random():
        call_count["n"] += 1
        return real_random()

    random.random = counting_random
    try:
        play_series("H", "L", HOME_PATTERN_BO3, s)
    finally:
        random.random = real_random

    assert call_count["n"] <= 3


def test_play_series_returns_one_of_the_two_teams():
    s = _standings({"H": 1500, "L": 1500})
    random.seed(42)
    for _ in range(50):
        winner = play_series("H", "L", HOME_PATTERN_BO5, s)
        assert winner in ("H", "L")


def test_play_series_home_pattern_constants_have_correct_lengths():
    assert len(HOME_PATTERN_BO3) == 3
    assert len(HOME_PATTERN_BO5) == 5
    assert len(HOME_PATTERN_BO7) == 7
    for pattern in (HOME_PATTERN_BO3, HOME_PATTERN_BO5, HOME_PATTERN_BO7):
        for entry in pattern:
            assert entry in ("H", "L")


def test_play_series_lower_seed_hosts_advantage_applied():
    """When the lower seed hosts a neutral-elo matchup, they should win
    those games more often than 50% (home court).
    """
    random.seed(7)
    s = _standings({"H": 1500, "L": 1500})

    lower_wins = sum(play_series("H", "L", ("L",), s) == "L" for _ in range(2000))
    assert lower_wins > 1050  # > 52.5%, well above 50/50 noise at N=2000


SEEDS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]


def _equal_standings() -> dict[str, "TeamStanding"]:
    return _standings({s: 1500 for s in SEEDS})


def test_simulate_playoffs_returns_required_keys():
    random.seed(0)
    result = simulate_playoffs(SEEDS, _equal_standings())
    assert set(result.keys()) == {
        "made_playoffs",
        "reached_semis",
        "reached_finals",
        "won_championship",
    }


def test_simulate_playoffs_cardinalities():
    """Each round has the expected number of teams."""
    random.seed(0)
    result = simulate_playoffs(SEEDS, _equal_standings())
    assert len(result["made_playoffs"]) == 8
    assert len(result["reached_semis"]) == 4
    assert len(result["reached_finals"]) == 2
    # won_championship is a single team name, not a set.
    assert isinstance(result["won_championship"], str)


def test_simulate_playoffs_subset_chain():
    """champion ∈ finals ⊂ semis ⊂ made_playoffs."""
    random.seed(0)
    for _ in range(20):
        result = simulate_playoffs(SEEDS, _equal_standings())
        assert result["won_championship"] in result["reached_finals"]
        assert result["reached_finals"].issubset(result["reached_semis"])
        assert result["reached_semis"].issubset(result["made_playoffs"])


def test_simulate_playoffs_made_playoffs_is_input_seeds():
    """made_playoffs should be exactly the seeded list (top 8)."""
    random.seed(0)
    result = simulate_playoffs(SEEDS, _equal_standings())
    assert result["made_playoffs"] == set(SEEDS)


def test_simulate_playoffs_top_seed_wins_against_weak_field():
    """Seed 1 with massive Elo edge should usually win the championship."""
    elos = {"S1": 1900}
    for s in SEEDS[1:]:
        elos[s] = 1300
    s = _standings(elos)
    random.seed(123)
    champ_wins = sum(
        simulate_playoffs(SEEDS, s)["won_championship"] == "S1" for _ in range(200)
    )
    assert champ_wins >= 170  # > 85%


# ---------------------------------------------------------------------------
# play_series resume + bracket-state tests
# ---------------------------------------------------------------------------


def test_play_series_resumes_from_starting_score():
    """A Bo3 already 1-0 to the higher seed should need at most 2 more games."""
    s = _standings({"H": 1500, "L": 1500})
    call_count = {"n": 0}
    real_random = random.random

    def counting_random():
        call_count["n"] += 1
        return real_random()

    random.random = counting_random
    try:
        play_series(
            "H",
            "L",
            HOME_PATTERN_BO3,
            s,
            starting_higher_wins=1,
            starting_lower_wins=0,
        )
    finally:
        random.random = real_random

    assert call_count["n"] <= 2


def test_play_series_short_circuits_when_starting_score_decides():
    """If the starting score already meets the win threshold, no simulation runs."""
    s = _standings({"H": 1500, "L": 1500})
    real_random = random.random
    called = {"n": 0}

    def boom():
        called["n"] += 1
        return real_random()

    random.random = boom
    try:
        winner = play_series(
            "H",
            "L",
            HOME_PATTERN_BO3,
            s,
            starting_higher_wins=2,
            starting_lower_wins=0,
        )
    finally:
        random.random = real_random

    assert winner == "H"
    assert called["n"] == 0


def test_reconstruct_bracket_state_no_games_is_empty_qf_slots():
    """Zero completed games → QF slots filled from seeded, SF/F empty, no winners."""
    state = reconstruct_bracket_state(SEEDS, [])
    for qf_id, hi_idx, lo_idx in [
        ("qf1", 0, 7),
        ("qf2", 3, 4),
        ("qf3", 2, 5),
        ("qf4", 1, 6),
    ]:
        assert state[qf_id].higher == SEEDS[hi_idx]
        assert state[qf_id].lower == SEEDS[lo_idx]
        assert state[qf_id].winner is None
    assert state["sf1"].higher is None
    assert state["f"].higher is None


def test_reconstruct_bracket_state_decided_qf_only_no_sf_match():
    """After a QF1 sweep with QF2 still open, SF1 slot stays empty."""
    games = [
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-15"},
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-17"},
    ]
    state = reconstruct_bracket_state(SEEDS, games)
    assert state["qf1"].winner == "S1"
    assert state["qf1"].higher_wins == 2
    assert state["sf1"].higher is None
    assert state["sf1"].lower is None


def test_reconstruct_bracket_state_in_progress_series():
    """Bo3 at 1-1 should record 1-1, no winner."""
    games = [
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-15"},
        {"team_a": "S1", "team_b": "S8", "winner_team": "S8", "date": "2026-09-17"},
    ]
    state = reconstruct_bracket_state(SEEDS, games)
    assert state["qf1"].higher_wins == 1
    assert state["qf1"].lower_wins == 1
    assert state["qf1"].winner is None


def test_simulate_playoffs_honors_decided_qf():
    """If S1 swept S8 in QF1, S8 never reaches semis in any sim."""
    games = [
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-15"},
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-17"},
    ]
    state = reconstruct_bracket_state(SEEDS, games)
    s = _equal_standings()
    random.seed(0)
    for _ in range(50):
        result = simulate_playoffs(SEEDS, s, bracket_state=state)
        assert "S1" in result["reached_semis"]
        assert "S8" not in result["reached_semis"]


def test_simulate_playoffs_resumes_in_progress_series():
    """QF1 at 1-1: both teams still possible semifinalists across many runs."""
    games = [
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-15"},
        {"team_a": "S1", "team_b": "S8", "winner_team": "S8", "date": "2026-09-17"},
    ]
    state = reconstruct_bracket_state(SEEDS, games)
    s = _equal_standings()
    random.seed(0)
    s1_wins = sum(
        "S1" in simulate_playoffs(SEEDS, s, bracket_state=state)["reached_semis"]
        for _ in range(200)
    )
    s8_wins = 200 - s1_wins
    assert 0 < s1_wins < 200
    assert 0 < s8_wins < 200


def test_run_monte_carlo_eliminated_team_zero_downstream():
    """End-to-end: a swept QF loser has 0% odds for semis/finals/championship."""
    from src.scoring.monte_carlo import (
        TeamStanding as TS,
        run_monte_carlo_simulation,
    )
    from src.scoring.tiebreakers import resolve_seeding

    real_names = [
        "Atlanta Dream",
        "Chicago Sky",
        "Indiana Fever",
        "Washington Mystics",
        "Dallas Wings",
        "Las Vegas Aces",
        "Minnesota Lynx",
        "New York Liberty",
    ]
    standings_dict = {
        n: {"wins": 30 - i, "losses": i, "elo": 1600 - i * 20, "h2h": {}}
        for i, n in enumerate(real_names)
    }
    ts = {
        n: TS(name=n, wins=d["wins"], losses=d["losses"], elo=d["elo"])
        for n, d in standings_dict.items()
    }
    seeded = resolve_seeding(ts)[:8]
    eliminated = seeded[7]
    advancing = seeded[0]
    completed = [
        {
            "team_a": advancing,
            "team_b": eliminated,
            "winner_team": advancing,
            "date": "2026-09-15",
        },
        {
            "team_a": advancing,
            "team_b": eliminated,
            "winner_team": advancing,
            "date": "2026-09-17",
        },
    ]
    bracket_state = reconstruct_bracket_state(seeded, completed)

    random.seed(7)
    result = run_monte_carlo_simulation(
        standings_dict, [], num_simulations=500, bracket_state=bracket_state
    )
    assert result.reach_semis[eliminated] == 0.0
    assert result.reach_finals[eliminated] == 0.0
    assert result.win_championship[eliminated] == 0.0
    assert result.reach_semis[advancing] == 1.0


def test_simulate_playoffs_ignores_mismatched_bracket_state():
    """When the observed state describes a different matchup than the sim's
    seeded slot, the state must not be applied — otherwise real wins get
    credited to unrelated teams.

    Bug scenario: the bracket_state was built assuming SEEDS order, but the
    sim is called with a different seeded order (mimicking a sim whose
    regular-season simulation drifted from observed seeding). The slot id
    `qf1` now points to a different matchup; applying `state["qf1"].winner`
    would put a team in reached_semis that isn't in its QF1 pair.
    """
    # Observed: S1 swept S8 in QF1.
    games = [
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-15"},
        {"team_a": "S1", "team_b": "S8", "winner_team": "S1", "date": "2026-09-17"},
    ]
    state = reconstruct_bracket_state(SEEDS, games)
    # Scrambled: S1 moved to seeded[1] (so in scrambled, QF1 is S2 vs S8,
    # and QF4 is S1 vs S7). S1 should never win QF1 here — only QF4.
    scrambled = ["S2", "S1", "S3", "S4", "S5", "S6", "S7", "S8"]
    # Make S1 dominant so it always wins QF4 (S1 vs S7). With the bug,
    # state["qf1"].winner=S1 would also be returned for QF1, putting S1
    # twice into the reached_semis set — yielding a 3-element set.
    elos = {n: 1500 for n in scrambled}
    elos["S1"] = 1900
    s = _standings(elos)

    random.seed(0)
    for _ in range(20):
        result = simulate_playoffs(scrambled, s, bracket_state=state)
        # Without the validation guard, S1 would be the QF1 winner (from
        # mismatched state) AND the QF4 winner (legitimately), collapsing
        # to 3 distinct names.
        assert len(result["reached_semis"]) == 4
        # QF1 in scrambled is S2 vs S8 — winner must be one of those, not S1.
        # (We can't observe QF winners directly; the cardinality check above
        # is the load-bearing assertion here.)


def test_play_series_recorder_none_is_noop():
    """With recorder=None (default), play_series behaves as before."""
    from src.scoring.monte_carlo import TeamStanding as TS
    from src.scoring.playoffs import HOME_PATTERN_BO3, play_series

    standings = {
        "A": TS(name="A", wins=20, losses=10, elo=1600),
        "B": TS(name="B", wins=10, losses=20, elo=1400),
    }
    random.seed(0)
    winner = play_series("A", "B", HOME_PATTERN_BO3, standings)
    assert winner in {"A", "B"}


def test_play_series_recorder_captures_per_game_outcomes():
    """With recorder provided, each played game appends a bool (did_higher_win)."""
    from src.scoring.monte_carlo import TeamStanding as TS
    from src.scoring.playoffs import HOME_PATTERN_BO3, play_series

    standings = {
        "A": TS(name="A", wins=20, losses=10, elo=1600),
        "B": TS(name="B", wins=10, losses=20, elo=1400),
    }
    recorder: list[bool] = []
    random.seed(0)
    winner = play_series("A", "B", HOME_PATTERN_BO3, standings, recorder=recorder)
    # Bo3 takes 2 or 3 games — recorder has one entry per game actually played.
    assert 2 <= len(recorder) <= 3
    assert all(isinstance(x, bool) for x in recorder)
    # Series winner is consistent with the recorded outcomes.
    higher_wins = sum(1 for r in recorder if r)
    lower_wins = sum(1 for r in recorder if not r)
    assert (higher_wins == 2 and winner == "A") or (lower_wins == 2 and winner == "B")


def test_play_series_recorder_skips_pre_played_games():
    """When resuming from starting wins, recorder only captures simulated games."""
    from src.scoring.monte_carlo import TeamStanding as TS
    from src.scoring.playoffs import HOME_PATTERN_BO3, play_series

    standings = {
        "A": TS(name="A", wins=20, losses=10, elo=1600),
        "B": TS(name="B", wins=10, losses=20, elo=1400),
    }
    recorder: list[bool] = []
    random.seed(0)
    # Series is 1-1 going into game 3 → only one game is simulated.
    play_series(
        "A",
        "B",
        HOME_PATTERN_BO3,
        standings,
        starting_higher_wins=1,
        starting_lower_wins=1,
        recorder=recorder,
    )
    assert len(recorder) == 1


def test_play_series_recorder_empty_when_series_already_decided():
    """Starting wins that already decide the series → no games played → empty recorder."""
    from src.scoring.monte_carlo import TeamStanding as TS
    from src.scoring.playoffs import HOME_PATTERN_BO3, play_series

    standings = {
        "A": TS(name="A", wins=20, losses=10, elo=1600),
        "B": TS(name="B", wins=10, losses=20, elo=1400),
    }
    recorder: list[bool] = []
    winner = play_series(
        "A",
        "B",
        HOME_PATTERN_BO3,
        standings,
        starting_higher_wins=2,
        starting_lower_wins=0,
        recorder=recorder,
    )
    assert winner == "A"
    assert recorder == []
