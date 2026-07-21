"""Pure classification + message composition for the live 'tune in' alerts.
No I/O — trivially unit-tested."""

from src.scoring.excitement import EXCITEMENT_CLOSE, EXCITEMENT_THRILLER

_BASE_URL = "https://wumbers.com"
_PREFIX = {"Thriller": "🔥 Thriller", "Close game": "👀 Close game"}


def classify_excitement(excitement: float | None) -> str | None:
    """Map a live excitement value to a label, or None if below Close."""
    if excitement is None:
        return None
    if excitement >= EXCITEMENT_THRILLER:
        return "Thriller"
    if excitement >= EXCITEMENT_CLOSE:
        return "Close game"
    return None


def compose_alert(game: dict, label: str) -> str:
    """One-line alert: '<prefix> — Away @ Home · away–home · excitement X.X\\n<url>'."""
    away = game.get("away_team", "")
    home = game.get("home_team", "")
    a, h = game.get("away_score", ""), game.get("home_score", "")
    score = f" · {a}–{h}" if a != "" and h != "" else ""
    exc = game.get("excitement")
    exc_str = f" · excitement {exc:.1f}" if isinstance(exc, (int, float)) else ""
    url = f"{_BASE_URL}/game/{game['espn_id']}"
    return f"{_PREFIX[label]} — {away} @ {home}{score}{exc_str}\n{url}"
