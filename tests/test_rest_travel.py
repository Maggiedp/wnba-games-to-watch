"""Tests for rest/travel feature primitives."""

import pytest

from src.scoring.rest_travel import (
    ARENA_COORDS,
    assert_all_teams_have_coords,
    compute_rest_travel_features,
    haversine_miles,
)


def test_haversine_zero_distance():
    nyc = (40.683, -73.975)
    assert haversine_miles(nyc, nyc) == 0.0


def test_haversine_known_city_pair():
    # NY (Brooklyn) -> LA (Crypto.com Arena) is ~2440 miles.
    ny = (40.683, -73.975)
    la = (34.043, -118.267)
    d = haversine_miles(ny, la)
    assert 2350 <= d <= 2520


def test_haversine_symmetric():
    a = (47.622, -122.354)  # Seattle
    b = (25.781, -80.188)  # arbitrary far point
    assert abs(haversine_miles(a, b) - haversine_miles(b, a)) < 1e-6


def test_arena_coords_cover_current_franchises():
    # Spot-check a few canonical names are present with plausible coords.
    for name in ("Las Vegas Aces", "New York Liberty", "Seattle Storm"):
        assert name in ARENA_COORDS
        lat, lon = ARENA_COORDS[name]
        assert -90 <= lat <= 90 and -180 <= lon <= 180


def _g(team_a, team_b, date, event_id="e"):
    return {"team_a": team_a, "team_b": team_b, "date": date, "event_id": event_id}


def test_features_first_game_is_neutral():
    games = [_g("Las Vegas Aces", "Seattle Storm", "2026-05-20")]
    feats = compute_rest_travel_features(games)
    assert feats[0]["rest_a"] is None and feats[0]["rest_b"] is None
    assert feats[0]["b2b_a"] == 0 and feats[0]["b2b_b"] == 0
    assert feats[0]["travel_a"] == 0.0 and feats[0]["travel_b"] == 0.0


def test_features_back_to_back_detected():
    # Aces play home on the 20th, then away at Seattle on the 21st (b2b, travel).
    games = [
        _g("Las Vegas Aces", "Chicago Sky", "2026-05-20", "e1"),
        _g("Seattle Storm", "Las Vegas Aces", "2026-05-21", "e2"),
    ]
    feats = compute_rest_travel_features(games)
    # Game 2: Aces are team_b (away). Their previous game was at home in Vegas.
    assert feats[1]["rest_b"] == 0  # 1 day between -> 0 rest = b2b
    assert feats[1]["b2b_b"] == 1
    assert feats[1]["travel_b"] > 800  # Vegas -> Seattle ~ 870 mi
    # Storm (team_a, home) first appearance -> neutral.
    assert feats[1]["rest_a"] is None


def test_features_rest_capped_at_four():
    games = [
        _g("Atlanta Dream", "Chicago Sky", "2026-05-01", "e1"),
        _g("Atlanta Dream", "Chicago Sky", "2026-05-20", "e2"),  # 19 days later
    ]
    feats = compute_rest_travel_features(games)
    assert feats[1]["rest_a"] == 4  # capped
    assert feats[1]["b2b_a"] == 0


def test_features_home_idle_zero_travel():
    games = [
        _g("Atlanta Dream", "Chicago Sky", "2026-05-01", "e1"),
        _g("Atlanta Dream", "Indiana Fever", "2026-05-05", "e2"),  # home again
    ]
    feats = compute_rest_travel_features(games)
    assert feats[1]["travel_a"] == 0.0  # stayed home in Atlanta
    assert feats[1]["rest_a"] == 3  # 4 days between -> 3 rest


def test_features_unsorted_input_is_sorted_first():
    games = [
        _g("Atlanta Dream", "Chicago Sky", "2026-05-20", "e2"),
        _g("Atlanta Dream", "Chicago Sky", "2026-05-01", "e1"),
    ]
    feats = compute_rest_travel_features(games)
    # Output aligns to chronological order: first entry is the 05-01 game.
    assert feats[0]["rest_a"] is None
    assert feats[1]["rest_a"] == 4


def test_coverage_assertion_passes_for_known_teams():
    games = [_g("Las Vegas Aces", "Seattle Storm", "2026-05-20")]
    assert_all_teams_have_coords(games)  # no raise


def test_coverage_assertion_raises_for_unknown_team():
    games = [_g("Las Vegas Aces", "Mystery BC", "2026-05-20")]
    with pytest.raises(KeyError, match="Mystery BC"):
        assert_all_teams_have_coords(games)
