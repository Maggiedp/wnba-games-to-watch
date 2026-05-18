"""Constants for WNBA Games to Watch."""


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


# Terminal statuses that explicitly mean a previously-final game is no
# longer final. ONLY these clear stored completion state:
#   POSTPONED   — game deferred to a future date.
#   CANCELED    — game won't happen.
#   RESCHEDULED — moved to a new date.
#
# STATUS_SCHEDULED and STATUS_IN_PROGRESS are deliberately NOT in this
# set even though they're "non-final" — for an already-stored final, a
# stale or partially rolled-back ESPN payload reporting scheduled /
# in-progress is more likely a transient glitch than a real un-final.
# Erasing real completion state on those would lose archive entries on
# every ESPN cache hiccup.
#
# STATUS_UNKNOWN is also excluded — that's the parser fallback for
# malformed responses.
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
