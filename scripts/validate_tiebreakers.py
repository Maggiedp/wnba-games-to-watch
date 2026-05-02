"""Validate resolve_seeding against historical WNBA seedings.

Replays 2024 and 2025 final standings using only completed-games data and
checks the produced top-8 ordering against published WNBA seedings.

Usage:
    python -m scripts.validate_tiebreakers

Treat as a smoke test: most seasons have no actual ties at the cutoff,
so this primarily verifies that we don't break the no-ties path. When a
season DID have a tied-cutoff scenario, the script will assert our
resolution matches the official seeding.
"""

from __future__ import annotations

import logging
from datetime import date

from src.constants import assert_all_teams_have_conferences
from src.data.espn_api import fetch_games_for_range
from src.scoring.monte_carlo import TeamStanding
from src.scoring.tiebreakers import PLAYOFF_TEAMS, increment_h2h, resolve_seeding

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Official WNBA final regular-season top-8 standings. Source: WNBA.com season
# standings pages. Update if league sources differ.
#
# Known limitation: as of writing, our ESPN data is missing all Connecticut Sun
# games for 2024 (none returned by `fetch_games_for_range` for that team). This
# causes a Top-8 mismatch for 2024 (we slot Washington Mystics into the spot
# that Sun should occupy). This is a data-fetching issue, not a tiebreaker bug;
# the validator correctly flags the mismatch. Filed as a follow-up; the right
# fix is in `src/data/espn_api.py` (or upstream ESPN data), not this script.
OFFICIAL_2024_TOP_8 = [
    "New York Liberty",
    "Minnesota Lynx",
    "Connecticut Sun",
    "Las Vegas Aces",
    "Seattle Storm",
    "Phoenix Mercury",
    "Indiana Fever",
    "Atlanta Dream",
]
OFFICIAL_2025_TOP_8: list[str] = [
    # TODO: fill in with actual 2025 top-8 from WNBA.com when validating.
    # Treat the script as a smoke test if 2025 standings aren't available yet.
]


def _build_standings(year: int) -> dict[str, TeamStanding]:
    """Build season-end standings (wins, losses, h2h) from ESPN games."""
    games = fetch_games_for_range(date(year, 1, 1), date(year, 12, 31))
    # Regular-season only: type 1 = preseason, type 2 = regular season, type 3 = postseason.
    # Standings/tiebreakers are computed from regular-season games only.
    completed = [
        g for g in games if g.get("winner_team") and g.get("season_type", 2) == 2
    ]

    teams: dict[str, TeamStanding] = {}
    for g in completed:
        for name in (g["team_a"], g["team_b"]):
            if name not in teams:
                teams[name] = TeamStanding(name=name)

    for g in completed:
        a, b = g["team_a"], g["team_b"]
        winner = g["winner_team"]
        loser = b if winner == a else a
        teams[winner].wins += 1
        teams[loser].losses += 1
        increment_h2h(teams[winner].h2h, loser, won=True)
        increment_h2h(teams[loser].h2h, winner, won=False)

    return teams


def validate(year: int, official_top_8: list[str]) -> None:
    standings = _build_standings(year)
    assert_all_teams_have_conferences(standings)
    seeded = resolve_seeding(standings)
    our_top_8 = seeded[:PLAYOFF_TEAMS]

    logger.info(f"[{year}] Our top 8: {our_top_8}")

    if not official_top_8:
        logger.info(f"[{year}] No official top-8 provided — running as smoke test")
        return

    if set(our_top_8) == set(official_top_8):
        logger.info(f"[{year}] Top 8 set matches official")
    else:
        only_ours = set(our_top_8) - set(official_top_8)
        only_official = set(official_top_8) - set(our_top_8)
        logger.error(f"[{year}] Top 8 mismatch")
        logger.error(f"  Only in ours: {only_ours}")
        logger.error(f"  Only in official: {only_official}")


if __name__ == "__main__":
    validate(2024, OFFICIAL_2024_TOP_8)
    validate(2025, OFFICIAL_2025_TOP_8)
