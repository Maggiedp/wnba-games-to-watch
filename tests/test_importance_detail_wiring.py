import json
from types import SimpleNamespace

from scripts.daily_update import _importance_detail_for_game
from src.scoring.monte_carlo import (
    FATE_CHAMPION,
    FATE_LOST_FINALS,
    FATE_LOST_QF,
    FATE_MISSED,
)


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
    fate_levels = [
        {"Aces": FATE_CHAMPION, "Liberty": FATE_LOST_FINALS},
        {"Aces": FATE_CHAMPION, "Liberty": FATE_LOST_FINALS},
        {"Aces": FATE_LOST_FINALS, "Liberty": FATE_CHAMPION},
        {"Aces": FATE_LOST_FINALS, "Liberty": FATE_CHAMPION},
    ]
    team_names = ["Aces", "Liberty"]
    postseason_slot_lookup = {"evtP": ("f", 1)}
    return (
        bracket_state,
        bracket_outcomes,
        fate_levels,
        team_names,
        postseason_slot_lookup,
    )


def _reg_inputs():
    # 1 remaining game (idx 0); Storm vs Sky. Sun is the mover (makes the
    # playoffs, losing in the QF, only when team_a/Storm wins).
    outcome_matrix = [[True], [True], [False], [False]]
    fate_levels = [
        {"Sun": FATE_LOST_QF},
        {"Sun": FATE_LOST_QF},
        {"Sun": FATE_MISSED},
        {"Sun": FATE_MISSED},
    ]
    remaining_event_index = {"evt1": 0}
    team_names = ["Sun", "Storm", "Sky"]
    return outcome_matrix, fate_levels, remaining_event_index, team_names


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
    # "Locked" reaches the same fate (lost_qf) regardless of outcome -> no
    # milestone moves, so no mover clears the threshold.
    fl = [{"Locked": FATE_LOST_QF}, {"Locked": FATE_LOST_QF}]
    game = {"event_id": "e", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    out = _importance_detail_for_game(game, om, fl, {"e": 0}, team_names=["Locked"])
    assert out is None


def test_unknown_event_returns_none():
    om, ps, idx, names = _reg_inputs()
    game = {"event_id": "missing", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    assert _importance_detail_for_game(game, om, ps, idx, team_names=names) is None


def test_postseason_payload_when_team_a_is_higher_seed():
    bs, bo, fl, names, lookup = _post_inputs()
    game = {"event_id": "evtP", "team_a": "Aces", "team_b": "Liberty", "season_type": 3}
    raw = _importance_detail_for_game(
        game,
        [],
        fl,
        {},
        bracket_state=bs,
        bracket_outcomes=bo,
        team_names=names,
        postseason_slot_lookup=lookup,
    )
    data = json.loads(raw)
    assert data["metric"] == "championship"
    assert data["if_a_team"] == "Aces" and data["if_b_team"] == "Liberty"
    aces = next(m for m in data["movers"] if m["team"] == "Aces")
    assert aces["level"] == "championship"
    # team_a is the higher seed → if_a is the "higher won" champ rate.
    assert aces["if_a"] == 1.0 and aces["if_b"] == 0.0


def test_postseason_orientation_flips_when_team_a_is_lower_seed():
    bs, bo, fl, names, lookup = _post_inputs()
    # Same series, but the matchup lists the lower seed (Liberty) as team_a.
    game = {"event_id": "evtP", "team_a": "Liberty", "team_b": "Aces", "season_type": 3}
    raw = _importance_detail_for_game(
        game,
        [],
        fl,
        {},
        bracket_state=bs,
        bracket_outcomes=bo,
        team_names=names,
        postseason_slot_lookup=lookup,
    )
    data = json.loads(raw)
    assert data["if_a_team"] == "Liberty"
    aces = next(m for m in data["movers"] if m["team"] == "Aces")
    # team_a is now the LOWER seed → if_a/if_b swap relative to higher/lower.
    assert aces["if_a"] == 0.0 and aces["if_b"] == 1.0


def test_zero_importance_suppresses_movers():
    """A corrected swing clamped to 0 means "all noise" — no stakes panel,
    even when a raw per-team delta clears the mover threshold."""
    # Sun's playoff rate: 26/50 if team_a wins vs 24/50 if team_b wins — a 0.04
    # raw delta (>= min_delta 0.03) that is pure finite-sample noise.
    outcome_matrix = [[s < 50] for s in range(100)]
    fate_levels = [
        {"Sun": FATE_LOST_QF if (s < 26 or 50 <= s < 74) else FATE_MISSED}
        for s in range(100)
    ]
    idx = {"evt1": 0}
    names = ["Sun", "Storm", "Sky"]
    game = {"event_id": "evt1", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    # Sanity: without the gate the mover would render.
    assert (
        _importance_detail_for_game(
            game, outcome_matrix, fate_levels, idx, team_names=names
        )
        is not None
    )
    assert (
        _importance_detail_for_game(
            game, outcome_matrix, fate_levels, idx, team_names=names, importance=0.0
        )
        is None
    )


def test_none_importance_does_not_suppress_movers():
    """importance=None means "not scored", not "zero stakes" — the gate must
    not fire (detail still governed by the existing lookups)."""
    om, ps, idx, names = _reg_inputs()
    game = {"event_id": "evt1", "team_a": "Storm", "team_b": "Sky", "season_type": 2}
    raw = _importance_detail_for_game(
        game, om, ps, idx, team_names=names, importance=None
    )
    assert raw is not None


def test_postseason_missing_bracket_inputs_returns_none():
    bs, bo, fl, names, lookup = _post_inputs()
    game = {"event_id": "evtP", "team_a": "Aces", "team_b": "Liberty", "season_type": 3}
    # postseason_slot_lookup omitted (None) → no payload.
    assert (
        _importance_detail_for_game(
            game,
            [],
            fl,
            {},
            bracket_state=bs,
            bracket_outcomes=bo,
            team_names=names,
        )
        is None
    )
