"""pbpstats-backed WNBA possession + box source (data.wnba.com legacy feed).

Source/ingest only — no RAPM or validation logic. Replaces the hand-rolled
wehoop/ESPN possession reconstruction with `pbpstats` parsing the data.wnba.com
legacy feed, which gives possession objects with an on-court 5-on-5 snapshot and
NBA-stack player ids that match the boxscore feed (single id space).

The legacy feed 403s without a browser User-Agent, so we monkeypatch
requests.Session.request at import time to inject a browser UA + wnba.com Referer.

Public API:
  wnba_game_ids(season)            -> list[str] of completed game ids
  stint_rows_for_game(gid, cache)  -> stint-row DataFrame (rapm.fit_rapm contract)
  game_box(gid, season)            -> per-player box DataFrame (single id space)
"""

import json
import os
import urllib.request

import pandas as pd

# --- UA monkeypatch (import-time, once) -------------------------------------
# data.wnba.com 403s without a browser User-Agent. Inject one on every requests
# Session call so pbpstats' data_nba web loader works for WNBA.
import requests  # noqa: E402

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
_REFERER = "https://www.wnba.com/"

if not getattr(requests.sessions.Session.request, "_wnba_ua_patched", False):
    _orig_request = requests.sessions.Session.request

    def _patched_request(self, method, url, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("User-Agent", _UA)
        headers.setdefault("Referer", _REFERER)
        return _orig_request(self, method, url, *args, headers=headers, **kwargs)

    _patched_request._wnba_ua_patched = True
    requests.sessions.Session.request = _patched_request

# Import pbpstats AFTER the monkeypatch is installed.
from pbpstats.client import Client  # noqa: E402

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "_stats_cache")
# pbpstats requires these subdirs under its working dir.
_PBP_SUBDIRS = (
    "pbp",
    "enhanced_pbp",
    "possessions",
    "boxscore",
    "game_details",
    "overrides",
    "schedule",
    "shots",
)

_SCHEDULE_URL = (
    "https://data.wnba.com/data/10s/v2015/json/mobile_teams/wnba/"
    "{season}/league/10_full_schedule.json"
)
_GAMEDETAIL_URL = (
    "https://data.wnba.com/data/10s/v2015/json/mobile_teams/wnba/"
    "{season}/scores/gamedetail/{gid}_gamedetail.json"
)


def _fetch_json_cached(url: str, cache_path: str) -> dict:
    """Fetch JSON from the legacy feed, caching the raw bytes to cache_path."""
    if os.path.exists(cache_path):
        with open(cache_path) as fh:
            return json.load(fh)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": _REFERER})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    with open(cache_path, "wb") as fh:
        fh.write(raw)
    return json.loads(raw)


def wnba_game_ids(season: int) -> list[str]:
    """Completed-game (stt == "Final") game ids for a season, in schedule order."""
    cache_path = os.path.join(_CACHE_DIR, "schedule", f"{season}_full_schedule.json")
    data = _fetch_json_cached(_SCHEDULE_URL.format(season=season), cache_path)
    ids: list[str] = []
    for month in data["lscd"]:
        for g in month["mscd"]["g"]:
            if g.get("stt") == "Final":
                ids.append(g["gid"])
    return ids


def _pbp_client(cache_dir: str) -> Client:
    for sub in _PBP_SUBDIRS:
        os.makedirs(os.path.join(cache_dir, sub), exist_ok=True)
    return Client(
        {
            "dir": cache_dir,
            "Possessions": {"source": "web", "data_provider": "data_nba"},
        }
    )


def _possession_offense_points(possession, prev_score: dict, team_ids: list) -> int:
    """Points the offense scored on this possession, via the cumulative-score delta.

    event.score is a defaultdict(int) of {team_id: cumulative_points}; a team
    absent from a given event's dict simply hasn't changed, so we carry forward
    prev_score. The end-of-possession cumulative for the offense team minus the
    prior possession's cumulative is the points scored on this possession.
    Mutates and returns the updated cumulative via the end_score dict.
    """
    end = dict(prev_score)
    for ev in possession.events:
        if ev.score:
            for t in team_ids:
                if t in ev.score:
                    end[t] = ev.score[t]
    off = possession.offense_team_id
    pts = end[off] - prev_score[off]
    return pts, end


def stint_rows_for_game(game_id: str, cache_dir: str) -> pd.DataFrame:
    """One stint-row per possession, matching the rapm.fit_rapm stint contract.

    Columns: game_id, date, off_players, def_players, possessions, points.
    Lineups come from the possession's first event's current_players (a clean
    5-on-5 snapshot); points from the cumulative-score delta over the possession.
    """
    client = _pbp_client(cache_dir)
    game = client.Game(game_id)
    possessions = game.possessions.items

    # Game date from the boxscore feed (schedule order season is unknown here, so
    # derive season from the game id: chars 3-4 are the 2-digit season year).
    season = 2000 + int(game_id[3:5])
    date = _game_date(game_id, season)

    team_ids = _team_ids(possessions)
    prev_score = {t: 0 for t in team_ids}

    rows = []
    n_bad_lineup = 0
    for p in possessions:
        pts, prev_score = _possession_offense_points(p, prev_score, team_ids)
        cp = p.events[0].current_players
        off_team = p.offense_team_id
        def_team = next((t for t in cp if t != off_team), None)
        if (
            def_team is None
            or len(cp.get(off_team, [])) != 5
            or len(cp.get(def_team, [])) != 5
        ):
            n_bad_lineup += 1
            continue
        rows.append(
            {
                "game_id": int(game_id),
                "date": date,
                "off_players": frozenset(cp[off_team]),
                "def_players": frozenset(cp[def_team]),
                "possessions": 1.0,
                "points": float(pts),
            }
        )

    if n_bad_lineup:
        # Surface how often a clean 5-on-5 was unavailable.
        print(
            f"[wnba_stats_source] game {game_id}: skipped {n_bad_lineup} "
            f"possession(s) lacking a clean 5-on-5"
        )
    return pd.DataFrame(rows)


def _team_ids(possessions) -> list:
    """The two team ids present across a game's possession events."""
    ids = set()
    for p in possessions:
        for ev in p.events:
            ids.update(ev.score.keys())
        ids.add(p.offense_team_id)
    return sorted(ids)


def _game_date(game_id: str, season: int):
    """Game date (datetime.date) from the gamedetail boxscore feed."""
    cache_path = os.path.join(_CACHE_DIR, "game_details", f"{game_id}_gamedetail.json")
    data = _fetch_json_cached(
        _GAMEDETAIL_URL.format(season=season, gid=game_id), cache_path
    )
    gdte = data["g"]["gdte"]
    return pd.to_datetime(gdte).date()


def game_box(game_id: str, season: int) -> pd.DataFrame:
    """Per-player box stats for played players (single NBA-stack id space).

    Columns: pid, plus_minus, points, rebounds, assists, steals, blocks,
    turnovers, minutes, game_id, game_date. Drops DNPs (zero minutes / null pm).
    """
    cache_path = os.path.join(_CACHE_DIR, "game_details", f"{game_id}_gamedetail.json")
    data = _fetch_json_cached(
        _GAMEDETAIL_URL.format(season=season, gid=game_id), cache_path
    )
    g = data["g"]
    game_date = pd.to_datetime(g["gdte"]).date()

    rows = []
    for side in ("hls", "vls"):
        for pl in g[side]["pstsg"]:
            pm = pl.get("pm")
            minutes = _to_minutes(pl.get("min"), pl.get("sec"), pl.get("totsec"))
            if pm is None or minutes <= 0:
                continue  # DNP
            rows.append(
                {
                    "pid": int(pl["pid"]),
                    "plus_minus": float(pm),
                    "points": float(pl.get("pts", 0)),
                    "rebounds": float(pl.get("reb", 0)),
                    "assists": float(pl.get("ast", 0)),
                    "steals": float(pl.get("stl", 0)),
                    "blocks": float(pl.get("blk", 0)),
                    "turnovers": float(pl.get("tov", 0)),
                    "minutes": minutes,
                    "game_id": int(game_id),
                    "game_date": game_date,
                }
            )
    return pd.DataFrame(rows)


def _to_minutes(minutes, sec, totsec) -> float:
    """Played minutes as a float. Prefers totsec (seconds) when present."""
    if totsec not in (None, ""):
        try:
            return float(totsec) / 60.0
        except (TypeError, ValueError):
            pass
    try:
        m = float(minutes or 0)
    except (TypeError, ValueError):
        m = 0.0
    try:
        s = float(sec or 0)
    except (TypeError, ValueError):
        s = 0.0
    return m + s / 60.0
