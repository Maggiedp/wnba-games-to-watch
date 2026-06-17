"""Focused guard test for the lineup invariant (spike-local; run via
`pytest scripts/spikes/`). Regression for the Codex adversarial-review finding:
the invariant must require exactly two teams, not just that present teams have 5."""

import pandas as pd

from scripts.spikes.darko_validate import validate_game

_PBP_COLS = [
    "game_id",
    "sequence_number",
    "type_text",
    "team_id",
    "athlete_id_1",
    "athlete_id_2",
    "period",
    "start_game_seconds_remaining",
    "end_game_seconds_remaining",
]


def _pbp(rows):
    return pd.DataFrame(rows, columns=_PBP_COLS)


def _box(rows):
    return pd.DataFrame(
        rows, columns=["game_id", "athlete_id", "team_id", "starter", "minutes"]
    )


def _two_events():
    # Two non-sub events spanning 24s each; no substitutions.
    return _pbp(
        [
            [1, 1, "Jump Shot", 10, 101, None, 1, 600, 576],
            [1, 2, "Defensive Rebound", 20, 201, None, 1, 576, 552],
        ]
    )


def test_invariant_perfect_when_both_teams_seeded():
    box = _box(
        [
            [1, 101, 10, True, 1.0],
            [1, 102, 10, True, 1.0],
            [1, 103, 10, True, 1.0],
            [1, 104, 10, True, 1.0],
            [1, 105, 10, True, 1.0],
            [1, 201, 20, True, 1.0],
            [1, 202, 20, True, 1.0],
            [1, 203, 20, True, 1.0],
            [1, 204, 20, True, 1.0],
            [1, 205, 20, True, 1.0],
        ]
    )
    rep = validate_game(_two_events(), box, 1)
    assert rep["invariant_ok_frac"] == 1.0
    assert rep["starters_ok"] is True


def test_invariant_drops_when_a_team_is_missing():
    # Only team 10 has starters seeded; team 20 is absent from the box.
    box = _box(
        [
            [1, 101, 10, True, 1.0],
            [1, 102, 10, True, 1.0],
            [1, 103, 10, True, 1.0],
            [1, 104, 10, True, 1.0],
            [1, 105, 10, True, 1.0],
        ]
    )
    rep = validate_game(_two_events(), box, 1)
    # Pre-fix this returned 1.0 (only the present team was checked). The guard now
    # treats a one-team snapshot as invalid.
    assert rep["invariant_ok_frac"] == 0.0
    assert rep["starters_ok"] is False
