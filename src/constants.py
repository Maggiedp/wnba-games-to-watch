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
    "LEAGUE PASS": Broadcasters.LEAGUE_PASS,
    "WNBA LEAGUE PASS": Broadcasters.LEAGUE_PASS,
    "NBA TV": Broadcasters.NBA_TV,
    "NBATV": Broadcasters.NBA_TV,
}
