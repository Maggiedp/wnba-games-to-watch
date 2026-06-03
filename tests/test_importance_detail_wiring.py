import json
from types import SimpleNamespace

from scripts.daily_update import _importance_detail_for_game


def _post_inputs():
    # Focal bracket game ("f", 1): higher seed (Aces) won sims 0,1 and was
    # champion both times; lower seed (Liberty) won sims 2,3 and was champion.
    bracket_state = {"f": SimpleNamespace(higher="Aces", lower="Liberty")}
    bracket_outcomes = [
        {("f", 1): True},
        {("f", 1): True},
        {("f", 1): False},
        {("f", 1): False},
    ]
    champions = ["Aces", "Aces", "Liberty", "Liberty"]
    team_names = ["Aces", "Liberty"]
    postseason_slot_lookup = {"evtP": ("f", 1)}
    return (
        bracket_state,
        bracket_outcomes,
        champions,
        team_names,
        postseason_slot_lookup,
    )


def _reg_inputs():
    # 1 remaining game (idx 0); Storm vs Sky. Sun is the mover.
    outcome_matrix = [[True], [True], [False], [False]]
    playoff_sets = [{"Sun"}, {"Sun"}, set(), set()]
    remaining_event_index = {"evt1": 0}
    team_names = ["Sun", "Storm", "Sky"]
    return outcome_matrix, playoff_sets, remaining_event_index, team_names


def test_regular_season_payload_shape():
    om, ps, idx, names = _reg_inputs()
    game = {"event_id": "evt1", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    raw = _importance_detail_for_game(game, om, ps, idx, team_names=names)
    data = json.loads(raw)
    assert data["metric"] == "playoffs"
    assert data["if_a_team"] == "Storm" and data["if_b_team"] == "Sky"
    sun = next(m for m in data["movers"] if m["team"] == "Sun")
    assert sun["if_a"] == 1.0 and sun["if_b"] == 0.0


def test_preseason_returns_none():
    om, ps, idx, names = _reg_inputs()
    game = {"event_id": "evt1", "team_a": "Storm", "team_b": "Sky", "season_type": 1}
    assert _importance_detail_for_game(game, om, ps, idx, team_names=names) is None


def test_regular_season_no_movers_returns_none():
    om = [[True], [False]]
    ps = [{"Locked"}, {"Locked"}]
    game = {"event_id": "e", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    out = _importance_detail_for_game(game, om, ps, {"e": 0}, team_names=["Locked"])
    assert out is None


def test_unknown_event_returns_none():
    om, ps, idx, names = _reg_inputs()
    game = {"event_id": "missing", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    assert _importance_detail_for_game(game, om, ps, idx, team_names=names) is None


def test_postseason_payload_when_team_a_is_higher_seed():
    bs, bo, champs, names, lookup = _post_inputs()
    game = {"event_id": "evtP", "team_a": "Aces", "team_b": "Liberty", "season_type": 3}
    raw = _importance_detail_for_game(
        game,
        [],
        [],
        {},
        bracket_state=bs,
        bracket_outcomes=bo,
        champions=champs,
        team_names=names,
        postseason_slot_lookup=lookup,
    )
    data = json.loads(raw)
    assert data["metric"] == "championship"
    assert data["if_a_team"] == "Aces" and data["if_b_team"] == "Liberty"
    aces = next(m for m in data["movers"] if m["team"] == "Aces")
    # team_a is the higher seed → if_a is the "higher won" champ rate.
    assert aces["if_a"] == 1.0 and aces["if_b"] == 0.0


def test_postseason_orientation_flips_when_team_a_is_lower_seed():
    bs, bo, champs, names, lookup = _post_inputs()
    # Same series, but the matchup lists the lower seed (Liberty) as team_a.
    game = {"event_id": "evtP", "team_a": "Liberty", "team_b": "Aces", "season_type": 3}
    raw = _importance_detail_for_game(
        game,
        [],
        [],
        {},
        bracket_state=bs,
        bracket_outcomes=bo,
        champions=champs,
        team_names=names,
        postseason_slot_lookup=lookup,
    )
    data = json.loads(raw)
    assert data["if_a_team"] == "Liberty"
    aces = next(m for m in data["movers"] if m["team"] == "Aces")
    # team_a is now the LOWER seed → if_a/if_b swap relative to higher/lower.
    assert aces["if_a"] == 0.0 and aces["if_b"] == 1.0


def test_postseason_missing_bracket_inputs_returns_none():
    bs, bo, champs, names, lookup = _post_inputs()
    game = {"event_id": "evtP", "team_a": "Aces", "team_b": "Liberty", "season_type": 3}
    # postseason_slot_lookup omitted (None) → no payload.
    assert (
        _importance_detail_for_game(
            game,
            [],
            [],
            {},
            bracket_state=bs,
            bracket_outcomes=bo,
            champions=champs,
            team_names=names,
        )
        is None
    )
