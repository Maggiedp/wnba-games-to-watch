"""`?season=` is bounded on every public route that takes it.

An unbounded int let a huge value (e.g. 10**20) reach the database and raise
(OverflowError on SQLite, out-of-range on Postgres), which surfaced as a 500.
"""

import pytest

_SEASON_ROUTES = [
    "/api/elo-history",
    "/api/replay",
    "/api/team-style",
    "/api/shot-making",
    "/api/calibration",
    "/api/player-shots?athlete_id=p1",
    "/player/p1",
    "/player/p1/og.png",
]


def _with_season(path: str, value: str) -> str:
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}season={value}"


@pytest.mark.parametrize("path", _SEASON_ROUTES)
@pytest.mark.parametrize("value", ["10000", "1996", "abc"])
def test_out_of_range_or_non_integer_season_is_422(client, path, value):
    assert client.get(_with_season(path, value)).status_code == 422


@pytest.mark.parametrize("path", _SEASON_ROUTES)
@pytest.mark.parametrize("value", ["1997", "9999"])
def test_season_bounds_are_inclusive(client, path, value):
    # An empty DB gives 200 or 404 depending on the route; only 422 is wrong.
    assert client.get(_with_season(path, value)).status_code != 422
