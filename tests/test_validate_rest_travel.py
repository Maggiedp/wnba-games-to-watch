"""Unit tests for the pure helpers in validate_rest_travel (no network)."""

from scripts.validate_rest_travel import (
    build_design_row,
    coef_to_elo_points,
)


def test_build_design_row_handles_none_rest():
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
    assert row[1] == 0 - 2  # None treated as 0
    assert row[2] == 0 - 1
    assert row[3] == (0.0 - 1500.0) / 1000.0
    assert row[4] == 0.0 - (-2.0)


def test_coef_to_elo_points_divides_by_elo_coef():
    # b_elo = 0.005, b_rest = 0.010 -> 2.0 Elo points per rest unit.
    assert abs(coef_to_elo_points(b_feature=0.010, b_elo=0.005) - 2.0) < 1e-9
