"""Constants for WNBA Games to Watch."""

# The season the live site reads and writes. Bump ONCE A YEAR, at season start.
#
# This bump was already mandatory before it had a name: fetch_bpi_ratings()
# builds /seasons/{CURRENT_SEASON}/powerindex, so a stale value silently serves
# last season's BPI rather than erroring.
#
# Deliberately NOT clock-derived. int(today_et()[:4]) would roll on Jan 1,
# blanking the completed list and rankings for the ~4 months before games
# start -- and, because these are default arguments bound at import, would
# freeze the year for the life of a Cloud Run instance anyway. Failing safe
# means a forgotten bump keeps showing last season, which is stale but
# coherent, so daily_update logs an error once the CALENDAR YEAR passes this
# one (it checks the clock, not the schedule -- see the detector's docstring).
#
# NOT the only season idiom in the repo, and deliberately so. There are three,
# and which one is right depends on what the surface CLAIMS to be showing:
#   1. this constant -- the season the site is about; pinned, bumped by hand.
#   2. clock_season() (src/data/espn_api.py) -- the calendar year. For surfaces
#      framed as current (/api/shot-making, /api/player-shots, /player/{id}),
#      where showing a finished season would mislabel it, so an empty offseason
#      board is the CORRECT state.
#   3. the newest POPULATED season -- for archives and retrospectives
#      (/api/replay, /api/team-style, /api/elo-history, /api/calibration),
#      which name the year they are showing and so can honestly serve last
#      season through the offseason.
# Do not assume this constant answers "which season" everywhere.
CURRENT_SEASON = 2026


class Broadcasters:
    """Broadcaster/streaming service names."""

    ESPN = "ESPN"
    NBC = "NBC"
    PRIME_VIDEO = "Prime Video"
    CBS = "CBS"
    PARAMOUNT_PLUS = "Paramount+"
    ION = "ION"
    USA_NETWORK = "USA Network"
    LEAGUE_PASS = "League Pass"
    NBA_TV = "NBA TV"

    ALL = [
        ESPN,
        NBC,
        PRIME_VIDEO,
        CBS,
        PARAMOUNT_PLUS,
        ION,
        USA_NETWORK,
        LEAGUE_PASS,
        NBA_TV,
    ]


class GameStatus:
    """ESPN status type names."""

    FINAL = "STATUS_FINAL"
    SCHEDULED = "STATUS_SCHEDULED"
    IN_PROGRESS = "STATUS_IN_PROGRESS"
    POSTPONED = "STATUS_POSTPONED"
    CANCELED = "STATUS_CANCELED"
    RESCHEDULED = "STATUS_RESCHEDULED"


# Terminal statuses that clear stored completion. STATUS_SCHEDULED,
# STATUS_IN_PROGRESS, and STATUS_UNKNOWN are deliberately excluded —
# for an already-stored final, those are more likely a transient ESPN
# glitch than a real un-finalization, and erasing real completion
# state on every cache hiccup would lose archive entries.
UN_FINALIZE_STATUSES = frozenset(
    {
        GameStatus.POSTPONED,
        GameStatus.CANCELED,
        GameStatus.RESCHEDULED,
    }
)


# Maps raw ESPN broadcast name (uppercased) → canonical display name.
# ESPN uses abbreviations like "ESPN2", "NBCSN", "AMZN" in broadcast data.
BROADCASTER_NORMALIZE: dict[str, str] = {
    "ESPN": Broadcasters.ESPN,
    "ESPN2": Broadcasters.ESPN,
    "ESPNU": Broadcasters.ESPN,
    "ABC": Broadcasters.ESPN,
    "NBC": Broadcasters.NBC,
    "NBCSN": Broadcasters.NBC,
    "PEACOCK": Broadcasters.NBC,
    "PRIME VIDEO": Broadcasters.PRIME_VIDEO,
    "AMZN": Broadcasters.PRIME_VIDEO,
    "AMAZON": Broadcasters.PRIME_VIDEO,
    "CBS": Broadcasters.CBS,
    "PARAMOUNT+": Broadcasters.PARAMOUNT_PLUS,
    "ION": Broadcasters.ION,
    "USA": Broadcasters.USA_NETWORK,
    "USA NETWORK": Broadcasters.USA_NETWORK,
    "USA NET": Broadcasters.USA_NETWORK,
    "LEAGUE PASS": Broadcasters.LEAGUE_PASS,
    "WNBA LEAGUE PASS": Broadcasters.LEAGUE_PASS,
    "NBA TV": Broadcasters.NBA_TV,
    "NBATV": Broadcasters.NBA_TV,
}


# WNBA conference assignments. Names match ESPN displayName (verified against
# `team_a` / `team_b` strings stored on Game rows). Update if the league
# realigns or expands.
TEAM_CONFERENCES: dict[str, str] = {
    "Atlanta Dream": "East",
    "Chicago Sky": "East",
    "Connecticut Sun": "East",
    "Indiana Fever": "East",
    "New York Liberty": "East",
    "Toronto Tempo": "East",
    "Washington Mystics": "East",
    "Dallas Wings": "West",
    "Golden State Valkyries": "West",
    "Las Vegas Aces": "West",
    "Los Angeles Sparks": "West",
    "Minnesota Lynx": "West",
    "Phoenix Mercury": "West",
    "Portland Fire": "West",
    "Seattle Storm": "West",
}


# Companion lookup so callers don't hardcode the conference XOR.
OTHER_CONFERENCE: dict[str, str] = {"East": "West", "West": "East"}


def assert_all_teams_have_conferences(standings: dict) -> None:
    """Fail fast if a team in standings is missing from TEAM_CONFERENCES.

    A silent KeyError deep in the tiebreaker chain would be hard to debug.
    Call this once at the top of any function that runs tiebreakers.
    """
    missing = [name for name in standings if name not in TEAM_CONFERENCES]
    if missing:
        raise KeyError(
            f"Teams missing from TEAM_CONFERENCES: {missing}. "
            f"Add them to src/constants.py."
        )
