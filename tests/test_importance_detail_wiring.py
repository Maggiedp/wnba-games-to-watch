import json
from scripts.daily_update import _importance_detail_for_game


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
