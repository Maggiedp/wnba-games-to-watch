"""Rest/travel feature primitives for the Elo win-probability investigation.

Pure, dependency-free (math only). Location of a game is the HOME team's city
(team_a per the ESPN _parse_event convention). Coordinates are city/arena-level
lat/long — precise enough for great-circle distance at league scale.

Franchise relocations inside the backtest window are handled by KEY, not by
season: ESPN reports the era-correct franchise name (e.g. "San Antonio Stars"
for 2016-17, "Las Vegas Aces" from 2018), so historical names get their own
entries. The coverage assertion (assert_all_teams_have_coords) fails fast if a
team appearing in the data is missing here - add its canonical name then.
"""

from __future__ import annotations

import math
from datetime import date as _date

# Canonical team name -> (latitude, longitude) of home arena/city.
# Canonical names match src.data.espn_api._canonical_name output.
ARENA_COORDS: dict[str, tuple[float, float]] = {
    # 2026 franchises
    "Atlanta Dream": (33.749, -84.388),
    "Chicago Sky": (41.878, -87.630),
    "Connecticut Sun": (41.491, -72.090),  # Uncasville, CT (Mohegan Sun)
    "Dallas Wings": (32.736, -97.108),  # Arlington, TX
    "Golden State Valkyries": (37.768, -122.388),  # San Francisco (Chase Center)
    "Indiana Fever": (39.764, -86.155),  # Indianapolis
    "Las Vegas Aces": (36.103, -115.178),
    "Los Angeles Sparks": (34.043, -118.267),
    "Minnesota Lynx": (44.979, -93.276),  # Minneapolis
    "New York Liberty": (40.683, -73.975),  # Brooklyn (Barclays)
    "Phoenix Mercury": (33.446, -112.071),
    "Seattle Storm": (47.622, -122.354),
    "Washington Mystics": (38.868, -76.986),  # SE Washington, DC
    "Toronto Tempo": (43.634, -79.420),  # Coca-Cola Coliseum
    "Portland Fire": (45.532, -122.667),  # Moda Center
    # Historical franchise names inside / near the 2016+ window
    "San Antonio Stars": (29.427, -98.437),  # -> Las Vegas Aces (2018)
    "Tulsa Shock": (36.154, -95.992),  # -> Dallas Wings (2016)
}

_EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


_REST_CAP = 4


def _to_date(s: str) -> _date:
    return _date.fromisoformat(s)


def _tz_hours(lon: float) -> float:
    """Approximate timezone offset (hours) from longitude. East is greater."""
    return lon / 15.0


def compute_rest_travel_features(games: list[dict]) -> list[dict]:
    """Per-game rest/travel features for both teams, aligned to chronological order.

    Input games each need: team_a (home), team_b (away), date (ISO), event_id.
    Sorted by (date, event_id) internally — the returned list is in that order.
    Each entry: {rest_a, rest_b, b2b_a, b2b_b, travel_a, travel_b, tz_a, tz_b}
    where rest_* is int|None (None = team's first game in the stream; capped at 4
    days), travel_* is float miles (0.0 on a team's first game), and tz_* is the
    signed timezone offset crossed since the team's previous game (+east, derived
    from longitude; 0.0 on a team's first game). The game's location is the home
    team's (team_a) city.
    """
    ordered = sorted(games, key=lambda g: (g.get("date", ""), g.get("event_id", "")))
    last: dict[str, tuple[_date, tuple[float, float]]] = {}  # team -> (date, coords)
    out: list[dict] = []

    for g in ordered:
        ta, tb = g["team_a"], g["team_b"]
        game_date = _to_date(g["date"])
        game_coords = ARENA_COORDS[ta]  # location = home team's city

        entry: dict = {}
        for team, key in ((ta, "a"), (tb, "b")):
            prev = last.get(team)
            if prev is None:
                entry[f"rest_{key}"] = None
                entry[f"b2b_{key}"] = 0
                entry[f"travel_{key}"] = 0.0
                entry[f"tz_{key}"] = 0.0
            else:
                prev_date, prev_coords = prev
                rest = min((game_date - prev_date).days - 1, _REST_CAP)
                rest = max(rest, 0)
                entry[f"rest_{key}"] = rest
                entry[f"b2b_{key}"] = 1 if rest == 0 else 0
                entry[f"travel_{key}"] = haversine_miles(prev_coords, game_coords)
                entry[f"tz_{key}"] = _tz_hours(game_coords[1]) - _tz_hours(
                    prev_coords[1]
                )
            last[team] = (game_date, game_coords)
        out.append(entry)

    return out


def assert_all_teams_have_coords(games: list[dict]) -> None:
    """Raise KeyError listing any team in `games` missing from ARENA_COORDS."""
    seen = {g["team_a"] for g in games} | {g["team_b"] for g in games}
    missing = sorted(t for t in seen if t not in ARENA_COORDS)
    if missing:
        raise KeyError(f"No arena coords for: {', '.join(missing)}")
