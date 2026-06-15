"""Unit tests for the pure helpers in validate_rest_travel (no network)."""

from scripts.validate_rest_travel import (
    build_design_row,
    coef_to_elo_points,
    rest_travel_delta,
)


def test_build_design_row_neutralizes_debut_rest():
    # rest_a is None (season debut for A): the rest differential is NEUTRAL (0),
    # NOT coerced to 0-2=-2 (which would read as A being tired). Other terms still
    # use the opponent's real fatigue (b2b, travel, tz).
    feat = {
        "rest_a": None,
        "rest_b": 2,
        "b2b_a": 0,
        "b2b_b": 1,
        "travel_a": 0.0,
        "travel_b": 1500.0,
        "tz_a": 0.0,
        "tz_b": -2.0,
    }
    row = build_design_row(x_elo=120.0, feat=feat)
    # [x_elo, rest_diff, b2b_diff, travel_diff_k, tz_diff]
    assert row[0] == 120.0
    assert row[1] == 0  # debut -> neutral rest differential
    assert row[2] == 0 - 1
    assert row[3] == (0.0 - 1500.0) / 1000.0
    assert row[4] == 0.0 - (-2.0)


def test_rest_diff_present_on_both_sides():
    feat = {
        "rest_a": 3,
        "rest_b": 1,
        "b2b_a": 0,
        "b2b_b": 0,
        "travel_a": 0.0,
        "travel_b": 0.0,
        "tz_a": 0.0,
        "tz_b": 0.0,
    }
    assert build_design_row(x_elo=0.0, feat=feat)[1] == 2  # 3 - 1


def test_coef_to_elo_points_divides_by_elo_coef():
    # b_elo = 0.005, b_rest = 0.010 -> 2.0 Elo points per rest unit.
    assert abs(coef_to_elo_points(b_feature=0.010, b_elo=0.005) - 2.0) < 1e-9


def test_rest_travel_delta_sums_weighted_diffs():
    feat = {
        "rest_a": 3,
        "rest_b": 1,
        "b2b_a": 0,
        "b2b_b": 1,
        "travel_a": 0.0,
        "travel_b": 2000.0,
        "tz_a": 0.0,
        "tz_b": -2.0,
    }
    # rest: 10*(3-1)=20 ; b2b: 5*(0-1)=-5 ; travel: -8*(0-2000)/1000=+16 ; tz: 3*(0-(-2))=6
    d = rest_travel_delta(
        feat, elo_rest=10.0, elo_b2b=5.0, elo_travel_per_1000=-8.0, elo_tz=3.0
    )
    assert abs(d - (20.0 - 5.0 + 16.0 + 6.0)) < 1e-9


def test_rest_travel_delta_neutral_when_symmetric():
    feat = {
        "rest_a": None,
        "rest_b": None,
        "b2b_a": 0,
        "b2b_b": 0,
        "travel_a": 0.0,
        "travel_b": 0.0,
        "tz_a": 0.0,
        "tz_b": 0.0,
    }
    assert rest_travel_delta(feat, 10.0, 5.0, -8.0, 3.0) == 0.0


def test_rest_travel_delta_neutralizes_debut_rest():
    # One-sided debut: A is debuting (rest None), B has 2 days rest. The rest
    # term must contribute 0 (not elo_rest*(0-2)), while B's other real features
    # still apply.
    feat = {
        "rest_a": None,
        "rest_b": 2,
        "b2b_a": 0,
        "b2b_b": 0,
        "travel_a": 0.0,
        "travel_b": 0.0,
        "tz_a": 0.0,
        "tz_b": 0.0,
    }
    assert (
        rest_travel_delta(
            feat, elo_rest=10.0, elo_b2b=5.0, elo_travel_per_1000=-8.0, elo_tz=3.0
        )
        == 0.0
    )
