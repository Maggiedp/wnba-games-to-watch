"""Tests for rest/travel feature primitives."""

from src.scoring.rest_travel import ARENA_COORDS, haversine_miles


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
