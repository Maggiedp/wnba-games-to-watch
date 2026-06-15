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
