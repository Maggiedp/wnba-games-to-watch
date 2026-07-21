"""Pure classification + message composition for the live 'tune in' alerts.
No I/O — trivially unit-tested."""

from datetime import datetime, timedelta

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


def filter_recent_tipoffs(games, now_utc, window_hours: int = 4):
    """Games whose tipoff (time_utc) falls in [now-window, now]. Drops null /
    unparseable time_utc. ET-key-agnostic (compares UTC instants), so it works
    across the midnight-ET boundary."""
    lo = now_utc - timedelta(hours=window_hours)
    out = []
    for g in games:
        raw = getattr(g, "time_utc", None)
        if not raw:
            continue
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if lo <= t <= now_utc:
            out.append(g)
    return out
