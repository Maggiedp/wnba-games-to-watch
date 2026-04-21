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
    """Game status values."""

    FINAL = "final"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "inprogress"
